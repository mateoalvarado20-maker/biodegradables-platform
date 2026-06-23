"""Test del helper que lleva las observaciones de cobranza de los asistentes
al reporte comercial (sección de cartera). Fix 2026-06-23."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INFO = "info@biodegradablesecuador.com"   # GYE
QUITO = "quito@biodegradablesecuador.com"  # UIO


def test_gestiones_por_ciudad(state_env):
    a = state_env.activity_state
    dr = pytest.importorskip("daily_report")

    # GYE (info@): un contactado con nota, un no-contactado
    a.add_adhoc("cobranza-acme-x", "📞 Cobranza: ACME SA — $100 (10d atraso)",
                user_email=INFO, tipo="diaria", meta=1)
    a.mark_daily("cobranza-acme-x", 1, user_email=INFO, notas="paga el viernes")
    a.add_adhoc("cobranza-beta-x", "📞 Cobranza: BETA CIA — $50 (5d atraso)",
                user_email=INFO, tipo="diaria", meta=1)
    a.mark_daily("cobranza-beta-x", 0, user_email=INFO, notas="no contesta")
    # UIO (quito@)
    a.add_adhoc("cobranza-gamma-x", "📞 Cobranza: GAMMA S.A. — $80 (8d atraso)",
                user_email=QUITO, tipo="diaria", meta=1)
    a.mark_daily("cobranza-gamma-x", 1, user_email=QUITO, notas="abona el lunes")

    gest = dr._cobranza_gestiones_por_ciudad()

    gye = gest["GYE"]
    assert gye[dr._norm_cliente("ACME SA")]["contactado"] is True
    assert "viernes" in gye[dr._norm_cliente("ACME SA")]["nota"]
    assert gye[dr._norm_cliente("BETA CIA")]["contactado"] is False
    assert "no contesta" in gye[dr._norm_cliente("BETA CIA")]["nota"]

    uio = gest["UIO"]
    assert uio[dr._norm_cliente("GAMMA S.A.")]["contactado"] is True
    # aislamiento por ciudad: GAMMA (UIO) NO aparece en GYE
    assert dr._norm_cliente("GAMMA S.A.") not in gye


def test_sin_gestiones_devuelve_vacio(state_env):
    dr = pytest.importorskip("daily_report")
    gest = dr._cobranza_gestiones_por_ciudad()
    assert gest == {"UIO": {}, "GYE": {}}
