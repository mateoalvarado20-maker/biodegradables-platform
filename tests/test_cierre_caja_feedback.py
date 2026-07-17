"""Feedback y recordatorio del cierre de caja (incidente 2026-07-16): el
submit de info@ llegó con la sección de caja vacía, el bot confirmó "✅" sin
avisar y el consolidado salió con "sin marcar". Ahora: (a) el chat avisa si
falta el cierre, (b) confirma con el total cuando sí se registró, (c) un job
a las 18:05 recuerda a quien no lo haya marcado."""
from __future__ import annotations

import asyncio
import importlib

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


INFO = "info@biodegradablesecuador.com"


class Ctx:
    def __init__(self):
        self.msgs = []

    async def send_activity(self, m):
        self.msgs.append(str(m))


def _form_base(activity_id="rev-percha"):
    import activity_state
    return {
        "ctx_user": INFO,
        "ctx_fecha": activity_state._today().isoformat(),
        "ctx_wk": activity_state.week_key(),
        f"estado__{activity_id}": "hecho",
        f"valor__{activity_id}": "1",
    }


def test_submit_sin_caja_advierte(bot):
    import activity_state
    activity_state.add_adhoc("rev-percha", "Revisar percha", user_email=INFO,
                             tipo="diaria", meta=1)
    ctx = Ctx()
    asyncio.run(bot._handle_checkin_submission(ctx, _form_base(), INFO))
    blob = "\n".join(ctx.msgs)
    assert "no llenaste el cierre de caja" in blob.lower()


def test_submit_con_caja_confirma_total(bot):
    import activity_state
    activity_state.add_adhoc("rev-percha", "Revisar percha", user_email=INFO,
                             tipo="diaria", meta=1)
    form = _form_base()
    form.update({"caja_b50": "2", "caja_b100": "", "caja_b20": "", "caja_b10": "",
                 "caja_b5": "", "caja_b1": "", "caja_m1": "", "caja_m050": "",
                 "caja_m025": "", "caja_m010": "", "caja_m005": "", "caja_m001": "",
                 "caja_notas": ""})
    ctx = Ctx()
    asyncio.run(bot._handle_checkin_submission(ctx, form, INFO))
    blob = "\n".join(ctx.msgs)
    assert "Cierre de caja registrado" in blob
    assert "$100.00" in blob
    assert "enviado a `" not in blob  # el mensaje viejo engañoso ya no existe
    assert "no llenaste el cierre" not in blob.lower()


def test_resubmit_sin_caja_no_advierte_si_ya_cerro(bot):
    """Si ya registró el cierre hoy y luego re-marca actividades sin tocar la
    caja, no hay que regañarla."""
    import activity_state
    activity_state.add_adhoc("rev-percha", "Revisar percha", user_email=INFO,
                             tipo="diaria", meta=1)
    hoy = activity_state._today().isoformat()
    activity_state.set_cierre_caja(INFO, hoy, {"b50": 2}, sucursal="Guayaquil")
    ctx = Ctx()
    asyncio.run(bot._handle_checkin_submission(ctx, _form_base(), INFO))
    blob = "\n".join(ctx.msgs)
    assert "no llenaste el cierre" not in blob.lower()


def test_recordatorio_solo_a_quien_falta(bot, monkeypatch):
    import activity_state
    hoy = activity_state._today().isoformat()
    # quito@ ya cerró; info@ no.
    activity_state.set_cierre_caja(
        "quito@biodegradablesecuador.com", hoy, {"b50": 2}, sucursal="Quito",
    )

    enviados: list[str] = []
    monkeypatch.setattr(bot, "_load_refs", lambda: {
        "activities": {u: {"fake": "ref"} for u in bot.CIERRE_CAJA_USERS}
    })

    class _FakeRef:
        def deserialize(self, d):
            return d

    monkeypatch.setattr(bot, "ConversationReference", _FakeRef)

    async def _fake_continue(ref, cb, bot_id=None):
        # el email no viaja en el cb — lo inferimos del orden del loop
        enviados.append("x")

    monkeypatch.setattr(
        bot.activities_adapter, "continue_conversation", _fake_continue
    )

    asyncio.run(bot.send_cierre_caja_recordatorio_job())
    assert len(enviados) == 1  # solo info@ (quito@ ya marcó)
