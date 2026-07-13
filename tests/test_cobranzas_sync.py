"""Sync de cobranzas al momento (2026-07-06): auto_assign_cobranzas ahora
sincroniza el state con la cartera REAL de Contifico — agrega nuevos, actualiza
montos y QUITA a los clientes que ya pagaron. Bug reportado: Gabriela B. y
Gladys veían en el card clientes que ya habían pagado (la asignación de las
7:30 vivía toda la semana sin actualizarse)."""
from __future__ import annotations

import asyncio
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    importlib.reload(send_ledger)
    import activity_state
    importlib.reload(activity_state)
    import teams_bot
    return importlib.reload(teams_bot)


GYE_USER = "info@biodegradablesecuador.com"
UIO_USER = "quito@biodegradablesecuador.com"


def _pulls(monkeypatch, bot, *, vencida=None, sin_credito=None):
    """Stub de los pulls de Contifico. `vencida`/`sin_credito` son dicts
    ciudad -> lista (o exception para simular fallo)."""
    vencida = vencida or {}
    sin_credito = sin_credito or {}

    def _venc(ciudad, n=5, **kw):
        v = vencida.get(ciudad, [])
        if isinstance(v, Exception):
            raise v
        return v

    def _sc(ciudad, *a, **kw):
        v = sin_credito.get(ciudad, [])
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(bot.contifico_client, "cartera_vencida_por_ciudad", _venc)
    monkeypatch.setattr(bot.contifico_client, "clientes_sin_credito_con_saldo", _sc)


def test_sync_quita_pagados_agrega_nuevos_y_actualiza_montos(bot, monkeypatch):
    import activity_state
    # Estado previo (asignación de la mañana): dos cobranzas de GYE
    activity_state.add_adhoc(
        "cobranza-cliente-pagado", "📞 Cobranza: CLIENTE PAGADO — $500 (10d atraso)",
        user_email=GYE_USER, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    activity_state.add_adhoc(
        "cobranza-acme-sa", "📞 Cobranza: ACME SA — $900 (12d atraso)",
        user_email=GYE_USER, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    # Una actividad normal que NO debe tocarse
    activity_state.add_adhoc(
        "revisar-percha", "Revisar percha", user_email=GYE_USER,
        tipo="diaria", meta=1, unidad="revisión",
    )

    # Cartera REAL de este momento: PAGADO ya no está; ACME sigue pero con
    # menos saldo (abonó); aparece un NUEVO sin crédito.
    _pulls(monkeypatch, bot, vencida={
        "GYE": [{"cliente": "ACME SA", "saldo_vencido": 400.0,
                 "dias_atraso_max": 13}],
        "UIO": [],
    }, sin_credito={
        "GYE": [{"cliente": "NUEVO SIN CREDITO", "saldo_pendiente": 120.0}],
        "UIO": [],
    })

    asyncio.run(bot.auto_assign_cobranzas())

    acts = activity_state.get_week(GYE_USER)["activities"]
    assert "cobranza-cliente-pagado" not in acts      # pagó → desaparece
    assert "cobranza-acme-sa" in acts                 # sigue debiendo
    assert "$400" in acts["cobranza-acme-sa"]["nombre"]   # monto actualizado
    assert "cobranza-sc-nuevo-sin-credito" in acts    # nuevo sin crédito
    assert "revisar-percha" in acts                   # lo demás intacto


def test_pull_fallido_no_quita_nada(bot, monkeypatch):
    import activity_state
    activity_state.add_adhoc(
        "cobranza-cliente-uio", "📞 Cobranza: CLIENTE UIO — $300 (8d atraso)",
        user_email=UIO_USER, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    # El pull de UIO falla; GYE responde vacío (OK)
    _pulls(monkeypatch, bot, vencida={
        "UIO": RuntimeError("Contifico timeout"),
        "GYE": [],
    }, sin_credito={
        "UIO": RuntimeError("Contifico timeout"),
        "GYE": [],
    })

    asyncio.run(bot.auto_assign_cobranzas())

    acts = activity_state.get_week(UIO_USER)["activities"]
    assert "cobranza-cliente-uio" in acts  # sin verdad de Contifico → no tocar


def test_fallo_total_levanta_error(bot, monkeypatch):
    _pulls(monkeypatch, bot, vencida={
        "UIO": RuntimeError("down"), "GYE": RuntimeError("down"),
    }, sin_credito={
        "UIO": RuntimeError("down"), "GYE": RuntimeError("down"),
    })
    with pytest.raises(RuntimeError):
        asyncio.run(bot.auto_assign_cobranzas())


def test_cobranza_marcada_hoy_tambien_se_quita_si_pago(bot, monkeypatch):
    """Si el asistente ya la marcó y LUEGO el cliente pagó, igual desaparece
    en el próximo sync — pagado es pagado."""
    import activity_state
    activity_state.add_adhoc(
        "cobranza-pago-tarde", "📞 Cobranza: PAGO TARDE — $200 (5d atraso)",
        user_email=GYE_USER, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    activity_state.mark_daily(
        "cobranza-pago-tarde", 1, user_email=GYE_USER, notas="dijo que pagaba hoy",
    )
    _pulls(monkeypatch, bot, vencida={"GYE": [], "UIO": []},
           sin_credito={"GYE": [], "UIO": []})

    asyncio.run(bot.auto_assign_cobranzas())

    acts = activity_state.get_week(GYE_USER)["activities"]
    assert "cobranza-pago-tarde" not in acts
