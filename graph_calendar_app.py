"""Cliente Microsoft Graph para Calendario — autenticación APP-ONLY.

A diferencia de `calendar_client.py` (auth delegada device-code, corre SOLO en
la PC de Mateo y necesita interacción), este módulo reutiliza el token de
Service Principal de `graph_mail` (client_credentials, scope `.default`) y por
eso CORRE EN AZURE sin intervención humana.

Lo usa el Activities Bot para crear/actualizar/borrar eventos en los calendarios
de Daniel y Gabriela Sánchez:
  - recordatorios de fecha límite de tareas (eventos all-day con reminder),
  - reuniones/eventos on-demand.

Requiere permiso de APLICACIÓN `Calendars.ReadWrite` con admin consent en el
App Registration del bot (el mismo que tiene `Mail.Send`). Se recomienda acotar
con una Application Access Policy a los buzones de Daniel + Gabriela
(least privilege). Detalle en `azure_setup_checklist.md`.

Sincronización: UNIDIRECCIONAL Bot→Calendario. No lee cambios del calendario.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

import graph_mail

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
DEFAULT_TZ = "America/Guayaquil"


def _graph(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict:
    """Request a Graph con token app-only de graph_mail (reutilizado/cacheado)."""
    token = graph_mail._get_token()
    r = httpx.request(
        method,
        f"{GRAPH_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json_body,
        params=params,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {method} {path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def _next_day_iso(date_iso: str) -> str:
    y, m, d = (int(x) for x in date_iso.split("-"))
    return (date(y, m, d) + timedelta(days=1)).isoformat()


def create_task_due_event(
    user_email: str,
    *,
    subject: str,
    due_date_iso: str,
    body_html: str = "",
    reminder_minutes_before: int = 120,
    timezone: str = DEFAULT_TZ,
    categories: list[str] | None = None,
) -> dict:
    """Crea un evento all-day en la fecha límite de una tarea.

    Devuelve el evento creado (incluye 'id' y 'webLink' para guardarlo en la
    tarea y poder actualizarlo/borrarlo después sin duplicar).
    """
    event: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "isAllDay": True,
        "start": {"dateTime": f"{due_date_iso}T00:00:00", "timeZone": timezone},
        "end": {"dateTime": f"{_next_day_iso(due_date_iso)}T00:00:00", "timeZone": timezone},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": reminder_minutes_before,
        "showAs": "free",
        "categories": categories or ["Tareas Activity Bot"],
    }
    return _graph("POST", f"/users/{user_email}/calendar/events", json_body=event)


def create_meeting(
    user_email: str,
    *,
    subject: str,
    start_iso: str,
    end_iso: str,
    body_html: str = "",
    attendees: list[str] | None = None,
    timezone: str = DEFAULT_TZ,
    is_online: bool = True,
) -> dict:
    """Crea una reunión/evento con hora (no all-day) en el calendario del user.

    attendees = lista de emails. Si is_online, agrega reunión de Teams.
    """
    event: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
    }
    if attendees:
        event["attendees"] = [
            {"emailAddress": {"address": a}, "type": "required"} for a in attendees
        ]
    if is_online:
        event["isOnlineMeeting"] = True
        event["onlineMeetingProvider"] = "teamsForBusiness"
    return _graph("POST", f"/users/{user_email}/calendar/events", json_body=event)


def create_reminder_event(
    user_email: str,
    *,
    subject: str,
    when_iso: str,
    body_html: str = "",
    reminder_minutes_before: int = 0,
    duration_minutes: int = 15,
    timezone: str = DEFAULT_TZ,
) -> dict:
    """Crea un evento corto con ALERTA en el calendario, que funciona como
    recordatorio (2026-06-18). `when_iso` = 'YYYY-MM-DDTHH:MM(:SS)' en hora local
    (sin offset). Lo usa el bot para que un recordatorio aparezca en el calendario
    de Outlook/Teams, además del mensaje de chat.
    """
    start_iso = when_iso
    try:
        end_iso = (
            datetime.fromisoformat(when_iso) + timedelta(minutes=duration_minutes)
        ).isoformat()
    except ValueError:
        end_iso = when_iso
    event: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "start": {"dateTime": start_iso, "timeZone": timezone},
        "end": {"dateTime": end_iso, "timeZone": timezone},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": reminder_minutes_before,
        "showAs": "free",
        "categories": ["Recordatorio Activity Bot"],
    }
    return _graph("POST", f"/users/{user_email}/calendar/events", json_body=event)


def update_task_due_event(
    user_email: str,
    event_id: str,
    *,
    due_date_iso: str,
    timezone: str = DEFAULT_TZ,
) -> dict:
    """Mueve un evento all-day a una nueva fecha (cuando cambia la fecha límite)."""
    patch = {
        "isAllDay": True,
        "start": {"dateTime": f"{due_date_iso}T00:00:00", "timeZone": timezone},
        "end": {"dateTime": f"{_next_day_iso(due_date_iso)}T00:00:00", "timeZone": timezone},
    }
    return _graph("PATCH", f"/users/{user_email}/events/{event_id}", json_body=patch)


def delete_event(user_email: str, event_id: str) -> None:
    """Borra un evento por id (toda la serie si es recurrente)."""
    _graph("DELETE", f"/users/{user_email}/events/{event_id}")


def find_event_by_subject(
    user_email: str, subject_substring: str, *, top: int = 50
) -> list[dict]:
    """Idempotencia: busca eventos cuyo subject contenga la cadena. Útil para
    no duplicar cuando una tarea perdió su calendar_event_id."""
    params = {
        "$top": str(top),
        "$select": "id,subject,start,end,webLink",
        "$orderby": "start/dateTime",
    }
    data = _graph("GET", f"/users/{user_email}/calendar/events", params=params)
    needle = subject_substring.lower()
    return [
        ev for ev in data.get("value", [])
        if needle in (ev.get("subject") or "").lower()
    ]


if __name__ == "__main__":
    # Smoke: token app-only + listar 5 eventos del primer usuario configurado.
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    import core_config
    target = core_config.CALENDAR_SYNC_USERS[0]
    print(f"App-only token + listar eventos de {target}...")
    params = {"$top": "5", "$select": "id,subject,start", "$orderby": "start/dateTime"}
    data = _graph("GET", f"/users/{target}/calendar/events", params=params)
    for ev in data.get("value", []):
        print(f"  - {ev.get('start', {}).get('dateTime', '?')} → {ev.get('subject')}")
