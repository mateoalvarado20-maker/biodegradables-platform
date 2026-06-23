"""Test de corregir_chocolates_stock (override limpio para corregir conteos).
Fix 2026-06-23 — info@ tenía un conteo confundido; se corrige a 8."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INFO = "info@biodegradablesecuador.com"


def test_correccion_fija_stock_actual(state_env):
    a = state_env.activity_state
    # Estado previo "confundido": stock inicial + entregas dispares
    a.set_chocolates_stock_inicial(INFO, 20)
    a.add_chocolates_entrega(INFO, "2026-06-22", 17)
    # Corrección a 8
    a.corregir_chocolates_stock(INFO, 8)
    rec = a.get_chocolates_semana(INFO)
    assert rec["stock_actual"] == 8
    assert rec["stock_inicial"] == 8
    assert rec["total_entregado"] == 0
    assert rec["total_recargado"] == 0
    assert "corregido_at" in rec


def test_correccion_override_aunque_ya_exista(state_env):
    a = state_env.activity_state
    # set_chocolates_stock_inicial es inmutable; la corrección SÍ sobrescribe
    a.set_chocolates_stock_inicial(INFO, 3)
    a.set_chocolates_stock_inicial(INFO, 99)  # no-op (first-write wins)
    assert a.get_chocolates_semana(INFO)["stock_actual"] == 3
    a.corregir_chocolates_stock(INFO, 8)
    assert a.get_chocolates_semana(INFO)["stock_actual"] == 8
