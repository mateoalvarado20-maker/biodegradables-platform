"""Fixes del resumen diario del equipo (2026-06-25):
1. Cobranzas: dedup por cliente (no repetir el mismo cliente varias veces).
2. José caja chica: 'saldo inicial del día' = cierre de ayer, no el fijo.
3. José salidas: header con duración / 'sin cerrar'."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INFO = "info@biodegradablesecuador.com"


def test_cobranza_dedup_por_cliente(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    hoy = a._today().isoformat()
    # Mismo cliente con 2 aids con fecha distinta (acumulación de la semana)
    a.add_adhoc("cobranza-acme-2026-06-23", "📞 Cobranza: ACME SA — $100 (10d atraso)",
                user_email=INFO, tipo="diaria", meta=1)
    a.add_adhoc("cobranza-acme-2026-06-24", "📞 Cobranza: ACME SA — $100 (10d atraso)",
                user_email=INFO, tipo="diaria", meta=1)
    a.mark_daily("cobranza-acme-2026-06-24", 1, user_email=INFO, notas="paga el viernes")

    html = aa._asistente_column_html(INFO, hoy)
    assert html.count("ACME SA") == 1          # NO repetido
    assert "paga el viernes" in html           # con su observación
    # contactada (una marca de hoy con valor>0)
    assert "Contactadas: 1" in html


def test_caja_saldo_inicial_refleja_cierre_de_ayer(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    hoy_d = a._today()
    hoy = hoy_d.isoformat()
    ayer = (hoy_d - timedelta(days=1)).isoformat()
    a.set_caja_chica_inicial(jose, 100.0)
    # Gasto de AYER 31 (cierre de ayer = 69), inyectado directo con ts de ayer
    st = a.load()
    st["users"][jose]["caja_chica"]["movimientos"].append(
        {"tipo": "gasto", "monto": 31.0, "descripcion": "ayer", "ts": ayer + "T15:00:00-05:00"}
    )
    a.save(st)
    # Gasto de HOY 10 → saldo actual = 100 - 31 - 10 = 59
    a.add_caja_chica_movimiento(jose, "gasto", 10.0, "hoy")

    html = aa._jose_consolidated_block_html(hoy)
    assert "Saldo inicial del día" in html
    assert "cierre de ayer" in html
    assert "$69.00" in html     # arranque de hoy = cierre de ayer (NO el inicial fijo 100)
    assert "$59.00" in html     # saldo actual


def test_jose_salida_header_con_duracion(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    jose = aa.JOSE_EMAIL_CONS
    hoy = "2026-06-22"  # lunes (evita rama de ausencia sabática)
    r = a.add_destino_adhoc(jose, cliente="ACME", direccion="x", tipo="entrega", fecha=hoy)
    a.start_ruta(jose, hoy)
    a.marcar_entrega(jose, r["factura_id"], entregado=True, cliente_label="ACME", fecha=hoy)
    a.end_ruta(jose, fecha=hoy)

    html = aa._jose_consolidated_block_html(hoy)
    assert "Salida #1" in html
    assert "min)" in html or "sin cerrar" in html
