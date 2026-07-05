"""José también recibe el card Quitar/Recolocar al llegar una actividad
delegada al 100% (2026-07-04) — antes solo el flujo del check-in lo disparaba
y él no tenía cómo sacar una actividad terminada de su card de ruta."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

teams_bot = pytest.importorskip("teams_bot")

JOSE = teams_bot.JOSE_EMAIL


class FakeCtx:
    def __init__(self):
        self.msgs = []

    async def send_activity(self, x):
        self.msgs.append(x)
        return None


def _card_texts(ctx: "FakeCtx") -> str:
    out = []
    for m in ctx.msgs:
        atts = getattr(m, "attachments", None)
        if atts:
            out.append(json.dumps(atts[0].content, ensure_ascii=False))
        else:
            out.append(str(m))
    return "\n".join(out)


def test_jose_al_100_recibe_card_quitar_recolocar(state_env, monkeypatch):
    a = state_env.activity_state
    a.add_adhoc("proj-jose", "Ordenar bodega", user_email=JOSE, tipo="unica")

    # El upsert del card de ruta refresca envíos desde Contifico — stub.
    async def _fake_upsert(*args, **kwargs):
        return False
    monkeypatch.setattr(teams_bot, "_upsert_jose_card", _fake_upsert)

    ctx = FakeCtx()
    form = {"avance__proj-jose": "100"}
    asyncio.run(teams_bot._handle_jose_intent(
        ctx, "jose_marcar_actividades", form, JOSE
    ))

    blob = _card_texts(ctx)
    assert "Registré" in blob
    # Llegó el card de seguimiento con las opciones
    assert "done_action__proj-jose" in blob
    assert "confirm_done" in blob

    # Y el submit de "quitar" la finaliza (mismo handler que el resto)
    form_done = {"ctx_user": JOSE, "done_action__proj-jose": "quitar"}
    asyncio.run(teams_bot._handle_done_activities(FakeCtx(), form_done, JOSE))
    wk = a.get_week(JOSE)
    assert a.task_effective_status(wk["activities"]["proj-jose"]) == "finalizada"


def test_jose_sin_100_no_recibe_card(state_env, monkeypatch):
    a = state_env.activity_state
    a.add_adhoc("proj-jose2", "Inventario", user_email=JOSE, tipo="unica")

    async def _fake_upsert(*args, **kwargs):
        return False
    monkeypatch.setattr(teams_bot, "_upsert_jose_card", _fake_upsert)

    ctx = FakeCtx()
    asyncio.run(teams_bot._handle_jose_intent(
        ctx, "jose_marcar_actividades", {"avance__proj-jose2": "60"}, JOSE
    ))
    assert "done_action__" not in _card_texts(ctx)
