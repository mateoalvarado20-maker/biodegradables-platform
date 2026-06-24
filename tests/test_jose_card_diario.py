"""Un card de ruta por día para José, editado en su lugar (2026-06-23):
- primer toque del día crea el card y guarda su activity_id;
- toques siguientes lo ACTUALIZAN (no crean uno nuevo);
- al crear el card de un día nuevo, el del día anterior se contrae (cierra).

El proyecto no tiene pytest-asyncio, así que las corutinas se corren con
asyncio.run() dentro de tests sincrónicos."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

teams_bot = pytest.importorskip("teams_bot")


class FakeResp:
    def __init__(self, _id):
        self.id = _id


class FakeTurnContext:
    """Captura send_activity / update_activity para inspeccionar el flujo."""
    def __init__(self):
        self.sent = []
        self.updated = []
        self._counter = 0

    async def send_activity(self, activity):
        self._counter += 1
        new_id = f"act-{self._counter}"
        self.sent.append((new_id, activity))
        return FakeResp(new_id)

    async def update_activity(self, activity):
        self.updated.append((getattr(activity, "id", None), activity))
        return FakeResp(getattr(activity, "id", None))


def test_state_helpers_card_id(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    assert a.get_ruta_card_id(jose, "2026-06-23") is None
    a.set_ruta_card_id(jose, "2026-06-23", "act-XYZ")
    assert a.get_ruta_card_id(jose, "2026-06-23") == "act-XYZ"


def test_prev_ruta_date_with_card(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    a.set_ruta_card_id(jose, "2026-06-22", "act-ayer")
    a.set_ruta_card_id(jose, "2026-06-23", "act-hoy")
    assert a.prev_ruta_date_with_card(jose, "2026-06-23") == "2026-06-22"
    assert a.prev_ruta_date_with_card(jose, "2026-06-22") is None


def test_upsert_crea_y_luego_actualiza(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    tc = FakeTurnContext()

    # Primer toque del día → crea (send) y guarda id
    created1 = asyncio.run(teams_bot._upsert_jose_card(tc, jose, skip_refresh=True))
    assert created1 is True
    assert len(tc.sent) == 1
    hoy = a._today().isoformat()
    stored = a.get_ruta_card_id(jose, hoy)
    assert stored == tc.sent[0][0]

    # Segundo toque → ACTUALIZA en su lugar (no crea otro)
    created2 = asyncio.run(teams_bot._upsert_jose_card(tc, jose, skip_refresh=True))
    assert created2 is False
    assert len(tc.sent) == 1  # no se envió otro card
    assert len(tc.updated) == 1
    assert tc.updated[0][0] == stored  # actualizó el mismo id


def test_upsert_no_crea_si_no_corresponde(state_env):
    jose = teams_bot.JOSE_EMAIL
    tc = FakeTurnContext()
    # create_if_absent=False y sin card previo → no envía nada
    created = asyncio.run(teams_bot._upsert_jose_card(
        tc, jose, skip_refresh=True, create_if_absent=False
    ))
    assert created is False
    assert tc.sent == []
    assert tc.updated == []


def test_cierra_card_dia_anterior(state_env):
    a = state_env.activity_state
    jose = teams_bot.JOSE_EMAIL
    # Simular que un día anterior tuvo card
    ayer = "2000-01-01"
    a.set_ruta_card_id(jose, ayer, "act-ayer")

    tc = FakeTurnContext()
    created = asyncio.run(teams_bot._upsert_jose_card(tc, jose, skip_refresh=True))
    assert created is True
    # Debe haber contraído el card de ayer (update sobre act-ayer)
    assert any(u[0] == "act-ayer" for u in tc.updated)
