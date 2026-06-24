"""Flujo 'actividad al 100%' (2026-06-24): al marcar una actividad semanal al
100%, el bot pregunta si quitarla del card o recolocarla para otro día.
- quitar → finaliza (deja de aparecer en el card)
- recolocar → avance 0, pendiente, con fecha elegida
- el card del check-in NO muestra finalizadas."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

teams_bot = pytest.importorskip("teams_bot")

USER = "malvarado@biodegradablesecuador.com"


class FakeCtx:
    def __init__(self):
        self.msgs = []

    async def send_activity(self, x):
        self.msgs.append(x)
        return None


def _add_proyecto(a, aid, nombre):
    a.add_adhoc(aid, nombre, user_email=USER, tipo="semanal")


def test_reset_task_para_rehacer(state_env):
    a = state_env.activity_state
    _add_proyecto(a, "proj-x", "Proyecto X")
    a.set_weekly_progress("proj-x", 100, user_email=USER)
    ent = a.reset_task_para_rehacer("proj-x", user_email=USER, fecha_limite="2026-07-01")
    assert ent["avance"] == 0
    assert ent["status"] == "pendiente"
    assert ent["fecha_limite"] == "2026-07-01"


def test_quitar_finaliza_y_desaparece_del_card(state_env):
    a = state_env.activity_state
    _add_proyecto(a, "proj-y", "Proyecto Y")
    a.set_weekly_progress("proj-y", 100, user_email=USER)
    form = {"ctx_user": USER, "done_action__proj-y": "quitar"}
    asyncio.run(teams_bot._handle_done_activities(FakeCtx(), form, USER))
    # Finalizada
    wk = a.get_week(USER)
    assert a.task_effective_status(wk["activities"]["proj-y"]) == "finalizada"
    # No aparece en el card
    import json
    card = teams_bot._build_checkin_card(USER).attachments[0].content
    assert "Proyecto Y" not in json.dumps(card, ensure_ascii=False)


def test_recolocar_con_fecha(state_env):
    a = state_env.activity_state
    _add_proyecto(a, "proj-z", "Proyecto Z")
    a.set_weekly_progress("proj-z", 100, user_email=USER)
    form = {
        "ctx_user": USER,
        "done_action__proj-z": "recolocar",
        "recolocar_fecha__proj-z": "2026-07-05",
    }
    asyncio.run(teams_bot._handle_done_activities(FakeCtx(), form, USER))
    ent = a.get_week(USER)["activities"]["proj-z"]
    assert ent["avance"] == 0
    assert ent["status"] == "pendiente"
    assert ent["fecha_limite"] == "2026-07-05"


def test_dejar_no_cambia_nada(state_env):
    a = state_env.activity_state
    _add_proyecto(a, "proj-w", "Proyecto W")
    a.set_weekly_progress("proj-w", 100, user_email=USER)
    form = {"ctx_user": USER, "done_action__proj-w": "dejar"}
    asyncio.run(teams_bot._handle_done_activities(FakeCtx(), form, USER))
    ent = a.get_week(USER)["activities"]["proj-w"]
    assert ent["avance"] == 100
    assert ent["status"] != "finalizada"


def test_done_card_tiene_opciones_y_fecha(state_env):
    card = teams_bot._build_done_activities_card(USER, [("proj-a", "Proyecto A")]).attachments[0].content
    ids = []
    def walk(n):
        if isinstance(n, dict):
            if n.get("id"):
                ids.append(n["id"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(card)
    assert "done_action__proj-a" in ids
    assert "recolocar_fecha__proj-a" in ids
