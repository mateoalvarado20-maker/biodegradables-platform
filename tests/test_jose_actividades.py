"""José ve y marca en su card de ruta las actividades diarias/semanales que le
delega gerencia (2026-06-25). info@/quito@ ya las ven en su check-in normal."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

teams_bot = pytest.importorskip("teams_bot")
JOSE = teams_bot.JOSE_EMAIL


class FakeResp:
    def __init__(self, _id):
        self.id = _id


class FakeCtx:
    def __init__(self):
        self.sent, self.updated, self._n = [], [], 0

    async def send_activity(self, x):
        self._n += 1
        self.sent.append(x)
        return FakeResp(f"act-{self._n}")

    async def update_activity(self, x):
        self.updated.append(getattr(x, "id", None))
        return FakeResp(getattr(x, "id", None))


def _ids(items):
    out = set()
    def walk(n):
        if isinstance(n, dict):
            if n.get("id"):
                out.add(n["id"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(items)
    return out


def test_sin_actividades_no_muestra_seccion(state_env):
    assert teams_bot._jose_actividades_items(JOSE) == []


def test_muestra_diaria_y_semanal_delegadas(state_env):
    a = state_env.activity_state
    a.add_adhoc("entregar-volantes", "Entregar volantes en ruta",
                user_email=JOSE, tipo="diaria", meta=20)
    a.add_adhoc("proyecto-bodega", "Ordenar bodega GYE",
                user_email=JOSE, tipo="semanal")
    items = teams_bot._jose_actividades_items(JOSE)
    ids = _ids(items)
    assert "estado__entregar-volantes" in ids
    assert "avance__proyecto-bodega" in ids
    # incluye el botón de guardar
    import json
    assert "jose_marcar_actividades" in json.dumps(items, ensure_ascii=False)


def test_handler_marca_actividades(state_env):
    a = state_env.activity_state
    a.add_adhoc("entregar-volantes", "Entregar volantes", user_email=JOSE,
                tipo="diaria", meta=20)
    a.add_adhoc("proyecto-bodega", "Ordenar bodega", user_email=JOSE, tipo="semanal")
    form = {
        "estado__entregar-volantes": "hecho",
        "valor__entregar-volantes": "20",
        "avance__proyecto-bodega": "50",
        "notas__proyecto-bodega": "media bodega",
    }
    tc = FakeCtx()
    asyncio.run(teams_bot._handle_jose_intent(tc, "jose_marcar_actividades", form, JOSE))
    wk = a.get_week(JOSE)
    hoy = a._today().isoformat()
    assert (wk["activities"]["entregar-volantes"]["log"][hoy]["valor"]) == 20
    assert wk["activities"]["proyecto-bodega"]["avance"] == 50
    assert any("Registré" in str(m) for m in tc.sent)
