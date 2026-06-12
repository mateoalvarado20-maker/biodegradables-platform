"""Fixtures compartidas de la suite.

Aísla cada test en un STATE_DIR temporal y recarga los módulos de state para
que sus STATE_PATH (calculados en import) apunten al directorio del test.
Ningún test toca `~/.claude-agent` real ni hace llamadas de red.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# La raíz del proyecto (los módulos viven planos en C:\Users\Mateo)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def state_env(tmp_path, monkeypatch):
    """STATE_DIR aislado + módulos de state recargados contra ese dir."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TRACKER_TARGET_USER", "mateo@test.local")

    import safe_json
    importlib.reload(safe_json)

    import activity_state
    import conversation_history
    import dispatch_state
    import reminders

    activity_state = importlib.reload(activity_state)
    reminders = importlib.reload(reminders)
    dispatch_state = importlib.reload(dispatch_state)
    conversation_history = importlib.reload(conversation_history)

    assert str(activity_state.STATE_PATH).startswith(str(tmp_path))
    assert str(reminders.STATE_PATH).startswith(str(tmp_path))

    yield SimpleNamespace(
        dir=tmp_path,
        safe_json=safe_json,
        activity_state=activity_state,
        reminders=reminders,
        dispatch_state=dispatch_state,
        conversation_history=conversation_history,
    )
