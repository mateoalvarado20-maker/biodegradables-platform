"""Dedup de cobranzas en la tabla de actividades diarias del consolidado
(_collaborator_block_html_v2). Fix 2026-06-25: auto_assign creaba un aid por día
(`cobranza-<cliente>-<fecha>`) y el mismo cliente salía repetido como varias
filas. Debe aparecer UNA sola vez."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INFO = "info@biodegradablesecuador.com"
_WED = date(2026, 6, 17)


def test_cobranza_no_se_repite_en_tabla_diaria(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    wk = a.week_key(_WED)
    hoy = _WED.isoformat()
    # Mismo cliente, 3 aids con fecha distinta (acumulación de la semana),
    # con días de atraso distintos en el nombre (como en producción).
    for d, atraso in (("2026-06-15", 18), ("2026-06-16", 19), ("2026-06-17", 20)):
        a.add_adhoc(
            f"cobranza-club-country-{d}",
            f"📞 Cobranza: CLUB DEPORTIVO COUNTRY — $218 ({atraso}d atraso)",
            user_email=INFO, tipo="diaria", meta=1, wk=wk,
        )
    # marcar solo la del día de hoy (_WED = 2026-06-17)
    a.mark_daily("cobranza-club-country-2026-06-17", 1, user_email=INFO,
                 notas="pagos semanales programados", wk=wk, fecha=hoy)

    html = aa._collaborator_block_html_v2(INFO, target_date=_WED)
    # El cliente aparece UNA sola vez (no 3)
    assert html.count("CLUB DEPORTIVO COUNTRY") == 1
    # y es la marcada (Hecha + su observación)
    assert "pagos semanales programados" in html
    assert "Hecha" in html
