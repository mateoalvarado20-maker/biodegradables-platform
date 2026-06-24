"""Fase 4 — el panel de presentación (demo_console) renderiza artefactos limpios.

Verifica que demo_console produce los HTML del reporte comercial, logística y
resumen del equipo bajo el tenant Andex, no vacíos y SIN datos del cliente real
(demo_console._write llama a demo_guard.assert_no_real_data, que abortaría).
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
def console(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_TODAY", TODAY.isoformat())
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DEMO_OUT", str(tmp_path / "out"))
    monkeypatch.setenv("CONTIFICO_API_TOKEN", "x")
    monkeypatch.setenv("HUBSPOT_TOKEN", "x")
    mods = [
        "core_config", "safe_json", "activity_state", "dispatch_state",
        "demo_seed", "demo_contifico", "demo_hubspot",
        "contifico_client", "hubspot_client",
        "daily_report", "daily_logistics_report", "ask_agent",
        "seed_demo_state", "demo_console",
    ]
    loaded = {}
    for m in mods:
        loaded[m] = importlib.reload(importlib.import_module(m))
    loaded["seed_demo_state"].seed_activities(TODAY)
    loaded["seed_demo_state"].seed_dispatch(TODAY)
    try:
        yield loaded["demo_console"]
    finally:
        monkeypatch.delenv("DEMO_MODE", raising=False)
        monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
        for m in ("core_config", "contifico_client", "hubspot_client",
                  "daily_report", "daily_logistics_report", "ask_agent"):
            importlib.reload(importlib.import_module(m))


def test_console_verify_config_ok(console):
    import demo_guard
    demo_guard.verify_demo_config()  # no levanta bajo andex/demo


def test_renders_comercial_y_logistica_y_equipo(console):
    for fn in ("render_comercial", "render_logistica", "render_equipo"):
        path = getattr(console, fn)()       # _write ya escanea anti-fuga
        data = Path(path).read_text(encoding="utf-8")
        assert len(data) > 500, f"{fn} produjo HTML vacío"


def test_index_lista_artefactos(console):
    paths = {"Comercial": console.render_comercial()}
    idx = console.write_index(paths)
    html = Path(idx).read_text(encoding="utf-8")
    assert "Andex" in html and "comercial.html" in html
