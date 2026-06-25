"""Fase 3 — estado del equipo pre-sembrado para el DEMO.

Siembra el estado de Andex en un STATE_DIR aislado y verifica:
  - activity_state queda poblado (actividades + cierres de caja),
  - dispatch_state queda con marcas de despacho,
  - el resumen consolidado del equipo renderiza con identidad Andex y SIN datos
    del cliente real (demo_guard).
"""
from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODAY = date(2026, 6, 24)


@pytest.fixture()
def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("DEMO_TODAY", TODAY.isoformat())
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("CONTIFICO_API_TOKEN", "x")
    monkeypatch.setenv("HUBSPOT_TOKEN", "x")
    mods = [
        "core_config", "safe_json", "activity_state", "dispatch_state",
        "demo_seed", "demo_contifico", "contifico_client",
    ]
    for m in mods:
        importlib.reload(importlib.import_module(m))
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    import seed_demo_state
    seed_demo_state = importlib.reload(seed_demo_state)
    seed_demo_state.seed_activities(TODAY)
    n_disp = seed_demo_state.seed_dispatch(TODAY)
    try:
        yield ask_agent, n_disp
    finally:
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
        importlib.reload(importlib.import_module("core_config"))
        importlib.reload(importlib.import_module("contifico_client"))
        importlib.reload(ask_agent)


def test_activities_and_cierres_seeded(seeded):
    import activity_state as st
    wk = st.week_key(TODAY)
    amora = st.get_week("amora@andexdemo.com", wk)
    assert "prospeccion-correos" in amora["activities"]
    # cierre de caja de la sucursal GYE (HOY — lo que resume el correo del día)
    cierre = st.get_cierre_caja("info@andexdemo.com", TODAY.isoformat())
    assert cierre and cierre["total"] > 0
    # cobranzas sembradas como actividades cobranza-* del asistente
    info_wk = st.get_week("info@andexdemo.com", wk)
    assert any(aid.startswith("cobranza-") for aid in info_wk["activities"])


def test_dispatch_seeded(seeded):
    _, n_disp = seeded
    import dispatch_state
    assert n_disp >= 3
    assert len(dispatch_state.load()) >= 3


def test_consolidated_summary_andex_sin_fuga(seeded):
    ask_agent, _ = seeded
    import demo_guard
    html = ask_agent._consolidated_daily_summary_html(
        ["cvega@andexdemo.com", "amora@andexdemo.com",
         "info@andexdemo.com", "quito@andexdemo.com"],
        target_date=TODAY,
    )
    assert len(html) > 500
    assert demo_guard.scan_for_real_data(html) == []
    # identidad Andex presente (algún nombre del equipo demo; títulos van en MAYÚS)
    low = html.lower()
    assert any(n in low for n in ("vega", "mora", "tipán", "tipan", "andex"))
    # Ejemplos de cómo llenan los colaboradores (lo que pidió el usuario):
    assert "Cierre de caja" in html and "TOTAL CONTADO" in html, "falta cierre de caja lleno"
    assert "Cobranza" in html, "faltan cobranzas"
