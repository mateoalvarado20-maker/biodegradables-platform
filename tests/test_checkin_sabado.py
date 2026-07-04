"""Check-in del sábado (2026-07-04): 12:00 para TODOS, sin cobranzas, y el
chofer recibe el card del asistente 1 de su sucursal (rotación de sábados).
"""
from __future__ import annotations

import datetime as dt
import importlib
import json

import pytest


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


def _card_json(activity) -> str:
    return json.dumps(activity.attachments[0].content, ensure_ascii=False)


def test_card_sabado_sin_cobranzas(bot, monkeypatch):
    """El sábado el card NO muestra ítems de cobranza; entre semana sí."""
    import activity_state
    user = "info@biodegradablesecuador.com"
    # Viernes y sábado de la MISMA semana ISO (2026-W28), para que las
    # adhoc creadas el viernes existan en la semana del sábado.
    sabado = dt.date(2026, 7, 11)
    viernes = dt.date(2026, 7, 10)

    monkeypatch.setattr(activity_state, "_today", lambda: viernes)
    activity_state.add_adhoc(
        "cobranza-acme", "📞 Cobranza: ACME SA — $500 (10d atraso)",
        user_email=user, tipo="diaria", meta=1, unidad="cliente contactado",
    )
    activity_state.add_adhoc(
        "otra-diaria", "Revisar inventario",
        user_email=user, tipo="diaria", meta=1, unidad="revisión",
    )

    txt = _card_json(bot._build_checkin_card(user))
    assert "estado__cobranza-acme" in txt  # viernes: cobranza visible

    monkeypatch.setattr(activity_state, "_today", lambda: sabado)
    txt = _card_json(bot._build_checkin_card(user))
    assert "estado__cobranza-acme" not in txt  # sábado: filtrada
    assert "estado__otra-diaria" in txt


def test_card_alt_sender_embebe_ctx_y_banner(bot):
    """El card de turno embebe ctx_alt (chofer autorizado) y el banner."""
    owner = "info@biodegradablesecuador.com"
    txt = _card_json(bot._build_checkin_card(
        owner, alt_sender="jsolorzano@biodegradablesecuador.com"
    ))
    assert '"ctx_alt": "jsolorzano@biodegradablesecuador.com"' in txt
    assert "Turno del sábado" in txt
    # Card normal: ctx_alt vacío y sin banner
    txt_normal = _card_json(bot._build_checkin_card(owner))
    assert '"ctx_alt": ""' in txt_normal
    assert "Turno del sábado" not in txt_normal


def test_submit_de_alt_sender_escribe_en_state_del_rol(bot):
    """José (ctx_alt) submitea el card de info@ → las marcas van al state de
    info@, no al de José. Un tercero sin ctx_alt sigue rechazado."""
    import asyncio

    import activity_state
    owner = "info@biodegradablesecuador.com"
    chofer = "jsolorzano@biodegradablesecuador.com"
    hoy = activity_state._today().isoformat()
    activity_state.add_adhoc(
        "tarea-x", "Tarea X", user_email=owner, tipo="diaria",
        meta=1, unidad="u",
    )

    sent: list[str] = []

    class _Ctx:
        async def send_activity(self, msg):
            sent.append(str(msg))

    form = {
        "ctx_user": owner,
        "ctx_alt": chofer,
        "ctx_fecha": hoy,
        "ctx_wk": activity_state.week_key(),
        "estado__tarea-x": "hecho",
        "valor__tarea-x": "1",
    }
    asyncio.run(bot._handle_checkin_submission(_Ctx(), dict(form), chofer))

    wk_owner = activity_state.get_week(owner)
    assert wk_owner["activities"]["tarea-x"]["log"].get(hoy), (
        "la marca debió quedar en el state del ROL (info@)"
    )
    state = activity_state.load()
    assert chofer not in state.get("users", {}), (
        "no debió crearse state para el chofer"
    )

    # Un tercero que NO es el ctx_alt sigue rechazado
    sent.clear()
    asyncio.run(bot._handle_checkin_submission(
        _Ctx(), dict(form), "quito@biodegradablesecuador.com"
    ))
    assert any("otro usuario" in m for m in sent)


def test_job_checkin_saturday_targets_todos(bot):
    """El job del sábado targetea oficina + sucursales (todos menos José,
    que recibe su card de turno por send_chofer_saturday_checkin)."""
    import core_config
    targets = (
        {u.lower() for u in core_config.CHECKIN_OFICINA}
        | {u.lower() for u in core_config.CHECKIN_SUCURSALES}
    )
    assert targets == {
        "malvarado@biodegradablesecuador.com",
        "gsanchez@biodegradablesecuador.com",
        "info@biodegradablesecuador.com",
        "quito@biodegradablesecuador.com",
    }
    assert core_config.CHECKIN_SATURDAY_SUCURSALES == (12, 0)


def test_asistente1_email_para_sucursal_del_chofer():
    import core_config
    suc = core_config.sucursal_for("jsolorzano@biodegradablesecuador.com")
    assert suc == "GYE"
    assert core_config.asistente1_email(suc) == "info@biodegradablesecuador.com"
    assert core_config.asistente1_email("NOEXISTE") == ""
