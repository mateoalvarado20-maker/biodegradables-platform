"""_classify_dailies debe deduplicar cobranzas por cliente (2026-06-25): los
aids `cobranza-<cliente>-<fecha>` acumulados en la semana hacían que el mismo
cliente se contara/listara repetido en 'Lo que requiere seguimiento'."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUITO = "quito@biodegradablesecuador.com"
_WED = date(2026, 6, 17)


def test_classify_dailies_dedup_cobranza(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    wk = a.week_key(_WED)
    hoy = _WED.isoformat()
    # Mismo cliente, 2 aids con fecha distinta, ambos no contactados hoy
    for d, atraso in (("2026-06-16", 5), ("2026-06-17", 6)):
        a.add_adhoc(
            f"cobranza-mindo-{d}",
            f"📞 Cobranza: MINDO CHOCOLATEMAKERS — $386 ({atraso}d atraso)",
            user_email=QUITO, tipo="diaria", meta=1, wk=wk,
        )
    a.mark_daily("cobranza-mindo-2026-06-17", 0, user_email=QUITO,
                 notas="No contactado", wk=wk, fecha=hoy)

    res = aa._classify_dailies(QUITO, hoy, wk=wk)
    # MINDO debe contar como UNA no_hecha (no dos)
    nombres = [p["nombre"] for p in res["problematicas"] if "MINDO" in p["nombre"]]
    assert len(nombres) == 1
    assert res["counts"]["no_hechas"] == 1


def test_classify_dailies_cobranza_contactada_no_es_problema(state_env):
    a = state_env.activity_state
    aa = pytest.importorskip("ask_agent")
    wk = a.week_key(_WED)
    hoy = _WED.isoformat()
    for d in ("2026-06-16", "2026-06-17"):
        a.add_adhoc(
            f"cobranza-acme-{d}", f"📞 Cobranza: ACME — $100 (5d atraso)",
            user_email=QUITO, tipo="diaria", meta=1, wk=wk,
        )
    # contactado hoy en uno de los aids → cuenta como hecha, NO problemática
    a.mark_daily("cobranza-acme-2026-06-17", 1, user_email=QUITO,
                 notas="pagó", wk=wk, fecha=hoy)

    res = aa._classify_dailies(QUITO, hoy, wk=wk)
    assert not any("ACME" in p["nombre"] for p in res["problematicas"])
    assert res["counts"]["hechas"] >= 1
