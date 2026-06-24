"""Fase 2 — datos sintéticos del entorno DEMO.

Verifica que con DEMO_MODE=1:
  - contifico_client / hubspot_client quedan servidos por los generadores demo,
  - los datos son COHERENTES (cartera total = vencida + no vencida; ciudades
    suman el total del día; meta = PY × factor),
  - son DETERMINISTAS (misma corrida, mismo resultado), y
  - NO contienen datos del cliente real (demo_guard).

Restaura los módulos a modo real al final.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODAY = date(2026, 6, 24)
AYER = TODAY - timedelta(days=1)


@pytest.fixture()
def demo(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_TODAY", TODAY.isoformat())
    monkeypatch.setenv("CONTIFICO_API_TOKEN", "x")
    monkeypatch.setenv("HUBSPOT_TOKEN", "x")
    import demo_seed
    importlib.reload(demo_seed)
    import demo_contifico
    importlib.reload(demo_contifico)
    import demo_hubspot
    importlib.reload(demo_hubspot)
    import contifico_client
    cc = importlib.reload(contifico_client)
    import hubspot_client
    hs = importlib.reload(hubspot_client)
    try:
        yield cc, hs
    finally:
        monkeypatch.delenv("DEMO_MODE", raising=False)
        importlib.reload(contifico_client)
        importlib.reload(hubspot_client)


def test_demo_mode_swaps_in_synthetic_data(demo):
    cc, _ = demo
    v = cc.ventas_dia(AYER)
    assert v["total"] > 0 and v["num_facturas"] > 0
    # los 4 vendedores ficticios (ninguno real)
    vend = {r["vendedor"] for r in cc.top_vendedores(TODAY.replace(day=1), TODAY, 5)}
    assert "Luis Maldonado" in vend
    assert not any("Sánchez" in n or "Alvarado" in n for n in vend)


def test_ciudades_suman_el_total_del_dia(demo):
    cc, _ = demo
    v = cc.ventas_dia(AYER)
    pc = cc.ventas_por_ciudad(AYER)["por_ciudad"]
    suma = round(pc["UIO"]["total"] + pc["GYE"]["total"] + pc["?"]["total"], 2)
    assert suma == pytest.approx(v["total"], abs=0.05)


def test_cartera_coherente(demo):
    cc, _ = demo
    k = cc.cartera_kpis(TODAY)
    assert k["cartera_total"] == pytest.approx(
        k["cartera_vencida"] + k["cartera_no_vencida"], abs=0.05
    )
    assert 0 < k["cartera_vencida"] < k["cartera_total"]
    # los buckets de antigüedad suman la cartera total
    buckets = sum(b["saldo"] for b in cc.cartera_antiguedad_buckets(TODAY))
    assert buckets == pytest.approx(k["cartera_total"], abs=0.5)


def test_meta_es_py_por_factor(demo):
    cc, _ = demo
    m = cc.cumplimiento_mes(TODAY)
    assert m["ventas_mtd"] > 0
    assert m["meta_mes"] == pytest.approx(
        m["ventas_mismo_mes_anio_anterior"] * 1.20, rel=0.001
    )


def test_determinista(demo):
    cc, _ = demo
    assert cc.ventas_dia(AYER) == cc.ventas_dia(AYER)
    assert cc.cartera_kpis(TODAY) == cc.cartera_kpis(TODAY)


def test_hubspot_demo_coherente(demo):
    _, hs = demo
    la = hs.leads_ayer()
    assert la["total"] == sum(la["by_source"].values())
    conv = hs.conversion_rate_30d()
    assert conv["cerrados_total"] == conv["ganados"] + conv["perdidos"]


def test_demo_data_sin_fuga_real(demo):
    cc, hs = demo
    import demo_guard
    prim = TODAY.replace(day=1)
    blob = json.dumps([
        cc.ventas_dia(AYER), cc.cumplimiento_mes(TODAY), cc.cartera_kpis(TODAY),
        cc.cartera_vencida_por_ciudad("GYE", 5, fecha_referencia=TODAY),
        cc.top_clientes(prim, TODAY, 10), cc.top_vendedores(prim, TODAY, 5),
        hs.leads_ayer(), hs.deals_stuck(),
    ], ensure_ascii=False, default=str)
    assert demo_guard.scan_for_real_data(blob) == []
