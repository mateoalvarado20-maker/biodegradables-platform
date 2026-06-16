"""Tests del cliente de calendario app-only (graph_calendar_app, Feature 2026-06-15).

No hace red: monkeypatch de graph_mail._get_token + httpx.request. Verifica que
usa el token app-only, el path /users/{email}/calendar/events y los cuerpos.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph_calendar_app


class _FakeResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.content = b"x" if payload is not None else b""
        self.text = "error simulado"

    def json(self):
        return self._payload or {}


@pytest.fixture()
def capture(monkeypatch):
    calls: list[dict] = []

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers,
                      "json": json, "params": params})
        return _FakeResp(200, {"id": "EV123", "webLink": "https://teams/ev", "value": []})

    monkeypatch.setattr(graph_calendar_app.graph_mail, "_get_token", lambda *a, **k: "FAKE")
    monkeypatch.setattr(graph_calendar_app.httpx, "request", fake_request)
    return calls


def test_app_only_token_y_path(capture):
    ev = graph_calendar_app.create_task_due_event(
        "dsanchez@biodegradablesecuador.com",
        subject="Tarea X", due_date_iso="2026-06-20",
    )
    assert ev["id"] == "EV123"
    call = capture[-1]
    assert call["method"] == "POST"
    assert call["url"].endswith(
        "/users/dsanchez@biodegradablesecuador.com/calendar/events"
    )
    assert call["headers"]["Authorization"] == "Bearer FAKE"
    body = call["json"]
    assert body["isAllDay"] is True
    assert body["start"]["dateTime"].startswith("2026-06-20")
    assert body["end"]["dateTime"].startswith("2026-06-21")  # día siguiente


def test_update_task_due_event_mueve_fecha(capture):
    graph_calendar_app.update_task_due_event(
        "gsanchez@biodegradablesecuador.com", "EV9", due_date_iso="2026-07-01"
    )
    call = capture[-1]
    assert call["method"] == "PATCH"
    assert call["url"].endswith("/users/gsanchez@biodegradablesecuador.com/events/EV9")
    assert call["json"]["start"]["dateTime"].startswith("2026-07-01")


def test_create_meeting_teams_y_attendees(capture):
    graph_calendar_app.create_meeting(
        "gsanchez@biodegradablesecuador.com",
        subject="Reunión", start_iso="2026-06-20T10:00",
        end_iso="2026-06-20T11:00", attendees=["x@y.com"],
    )
    body = capture[-1]["json"]
    assert body["isOnlineMeeting"] is True
    assert body["attendees"][0]["emailAddress"]["address"] == "x@y.com"


def test_find_by_subject_filtra(capture, monkeypatch):
    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        return _FakeResp(200, {"value": [
            {"id": "1", "subject": "📌 Tarea: Pagar luz"},
            {"id": "2", "subject": "Reunión equipo"},
        ]})
    monkeypatch.setattr(graph_calendar_app.httpx, "request", fake_request)
    found = graph_calendar_app.find_event_by_subject("dsanchez@x.com", "pagar luz")
    assert len(found) == 1 and found[0]["id"] == "1"


def test_error_status_lanza(capture, monkeypatch):
    monkeypatch.setattr(
        graph_calendar_app.httpx, "request",
        lambda *a, **k: _FakeResp(403, None),
    )
    with pytest.raises(RuntimeError):
        graph_calendar_app.delete_event("x@y.com", "EV1")
