"""Tests de la habilitación de calendario para Gabriela + recordatorios al
calendario real (2026-06-18)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph_calendar_app


# ---------- graph_calendar_app.create_reminder_event ----------

class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"id": "EV", "webLink": "https://teams/ev"}
        self.content = b"x"
        self.text = ""

    def json(self):
        return self._payload


def test_create_reminder_event(monkeypatch):
    calls = {}

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        calls["method"] = method
        calls["url"] = url
        calls["json"] = json
        return _FakeResp()

    monkeypatch.setattr(graph_calendar_app.graph_mail, "_get_token", lambda *a, **k: "FAKE")
    monkeypatch.setattr(graph_calendar_app.httpx, "request", fake_request)

    ev = graph_calendar_app.create_reminder_event(
        "dsanchez@biodegradablesecuador.com",
        subject="⏰ Recordatorio: pagar luz",
        when_iso="2026-06-20T08:00:00",
        body_html="pagar luz",
    )
    assert ev["webLink"]
    b = calls["json"]
    assert calls["method"] == "POST"
    assert calls["url"].endswith("/users/dsanchez@biodegradablesecuador.com/calendar/events")
    assert b["isReminderOn"] is True
    assert b["start"]["dateTime"] == "2026-06-20T08:00:00"
    # end = start + 15 min default
    assert b["end"]["dateTime"] == "2026-06-20T08:15:00"


# ---------- gating: Gabriela puede agendar; un colaborador normal no ----------

@pytest.fixture()
def ra(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "KNOWN_COLLABORATORS",
        "mateo:malvarado@biodegradablesecuador.com,"
        "gabriela:gsanchez@biodegradablesecuador.com",
    )
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import reminders
    importlib.reload(reminders)
    import core_config
    importlib.reload(core_config)
    import ask_agent
    return importlib.reload(ask_agent)


def _tool_names(ra, email):
    return {t["name"] for t in ra._tools_for_mode("activities", user_email=email)}


def test_gabriela_tiene_tools_de_agenda(ra):
    names = _tool_names(ra, "gsanchez@biodegradablesecuador.com")
    assert "create_calendar_meeting_for_collaborator" in names
    assert "schedule_reminder_for_collaborator" in names


def test_daniel_tiene_tools_de_agenda(ra):
    names = _tool_names(ra, "dsanchez@biodegradablesecuador.com")
    assert "create_calendar_meeting_for_collaborator" in names
    assert "schedule_reminder_for_collaborator" in names


def test_colaborador_normal_no_tiene_tools_de_agenda(ra):
    names = _tool_names(ra, "info@biodegradablesecuador.com")
    assert "create_calendar_meeting_for_collaborator" not in names
    assert "schedule_reminder_for_collaborator" not in names
