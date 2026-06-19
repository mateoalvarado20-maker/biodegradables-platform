"""Tests de los fixes 2026-06-19:
  #1 reconcile del snapshot de envíos (quita falsos positivos viejos de oficina).
  #2 helper de transporte de daily_logistics_report (no confunde TRANSPARENTE).
  #3 las cobranzas son tipo 'diaria' (mark_daily funciona, sin ValueError).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

U = "jose@biodegradablesecuador.com"
FECHA = "2026-06-19"


# ---------- #1 reconcile_envios_snapshot ----------

def test_reconcile_quita_falsos_positivos(state_env):
    a = state_env.activity_state
    a.set_envios_snapshot(U, {
        "real-1": {"factura_id": "real-1", "cliente": "REAL", "direccion_factura": "x"},
        "falso-2": {"factura_id": "falso-2", "cliente": "OFICINA", "direccion_factura": ""},
        "entregado-3": {"factura_id": "entregado-3", "cliente": "C", "direccion_factura": "y"},
    }, fecha=FECHA)
    # ad-hoc agregado a mano
    a.add_destino_adhoc(U, "Cliente adhoc", "dir adhoc", fecha=FECHA)
    # marcar entregado-3 como entregado (requiere salida abierta)
    a.start_ruta(U, fecha=FECHA)
    a.marcar_entrega(U, "entregado-3", True, fecha=FECHA)

    # fresh = solo real-1 (el filtro actual ya no considera 'falso-2' un envío)
    res = a.reconcile_envios_snapshot(U, {"real-1"}, fecha=FECHA)

    snap = a.get_ruta_dia(U, FECHA)["envios_snapshot"]
    assert "real-1" in snap            # está en fresh → se queda
    assert "entregado-3" in snap       # ya entregado → preservado
    assert "falso-2" not in snap       # falso positivo → removido
    assert any(k.startswith("adhoc-") for k in snap)  # ad-hoc → preservado
    assert res["removed"] == 1


def test_reconcile_sin_ruta_no_rompe(state_env):
    a = state_env.activity_state
    res = a.reconcile_envios_snapshot(U, {"x"}, fecha="2099-01-01")
    assert res["removed"] == 0


# ---------- #3 cobranza es 'diaria' (mark_daily funciona) ----------

def test_cobranza_diaria_se_marca(state_env):
    a = state_env.activity_state
    # auto_assign_cobranzas crea con tipo='diaria'; replicamos y marcamos.
    a.add_adhoc("cobranza-acme-2026-06-19", "📞 Cobranza: ACME — $100 (10d)",
                user_email="info@biodegradablesecuador.com", tipo="diaria",
                meta=1, unidad="cliente contactado")
    # mark_daily NO debe lanzar (sí lanzaría si fuera 'unica')
    rec = a.mark_daily("cobranza-acme-2026-06-19", 1,
                       user_email="info@biodegradablesecuador.com",
                       notas="paga el viernes")
    assert rec["valor"] == 1
    assert rec["notas"] == "paga el viernes"


# ---------- #2 helper de transporte (daily_logistics_report) ----------

def test_es_linea_transporte():
    import daily_logistics_report as dlr
    assert dlr._es_linea_transporte("TRANSPORTE") is True
    assert dlr._es_linea_transporte("TRANSP. EXT.-CB. 12%") is True
    assert dlr._es_linea_transporte("VASO 12 OZ TRANSPARENTE") is False
    assert dlr._es_linea_transporte("FUNDA DOYPACK TRANSPARENTE") is False
