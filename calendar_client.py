"""Cliente Microsoft Graph para Calendario.

Reutiliza la auth MSAL de pbi_cloud.py (mismo app registration, mismo token cache).

Scopes requeridos:
    Calendars.ReadWrite          → leer/escribir tu propio calendario
    Calendars.ReadWrite.Shared   → leer/escribir calendarios que otros comparten contigo

Para escribir en calendario de Daniel:
    1. App registration en Azure debe tener Calendars.ReadWrite.Shared (admin consent).
    2. Daniel debe compartir su calendario contigo con permiso Editor.
"""
from __future__ import annotations

from typing import Any

import httpx

from pbi_cloud import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
CAL_RW_SCOPES = [
    "https://graph.microsoft.com/Calendars.ReadWrite",
    "https://graph.microsoft.com/Calendars.ReadWrite.Shared",
]


def _graph_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    interactive_ok: bool = True,
) -> dict:
    token = get_token(CAL_RW_SCOPES, interactive_ok=interactive_ok)
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


def _create_recurring_all_day_event(
    *,
    user_email: str | None,
    subject: str,
    body_html: str,
    start_date_iso: str,
    recurrence_pattern: dict,
    reminder_minutes_before: int,
    timezone: str,
    categories: list[str] | None,
    interactive_ok: bool,
) -> dict:
    year, month, day = (int(x) for x in start_date_iso.split("-"))
    end_year, end_month, end_day = _next_day(year, month, day)
    end_date_iso = f"{end_year:04d}-{end_month:02d}-{end_day:02d}"

    event: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "isAllDay": True,
        "start": {"dateTime": f"{start_date_iso}T00:00:00", "timeZone": timezone},
        "end": {"dateTime": f"{end_date_iso}T00:00:00", "timeZone": timezone},
        "isReminderOn": True,
        "reminderMinutesBeforeStart": reminder_minutes_before,
        "recurrence": {
            "pattern": recurrence_pattern,
            "range": {"type": "noEnd", "startDate": start_date_iso},
        },
        "showAs": "free",
    }
    if categories:
        event["categories"] = categories

    path = f"/users/{user_email}/calendar/events" if user_email else "/me/calendar/events"
    return _graph_request("POST", path, json_body=event, interactive_ok=interactive_ok)


def create_yearly_all_day_event(
    *,
    user_email: str | None,
    subject: str,
    body_html: str,
    start_date_iso: str,
    reminder_minutes_before: int = 14400,
    timezone: str = "America/Guayaquil",
    categories: list[str] | None = None,
    interactive_ok: bool = True,
) -> dict:
    """Crea un evento de día completo con recurrencia anual.

    Args:
        user_email: si None, lo crea en /me. Si tiene valor, lo crea en
            /users/{user_email}/calendar/events (requiere calendario compartido).
        subject: título del evento.
        body_html: cuerpo HTML.
        start_date_iso: fecha de inicio en formato YYYY-MM-DD (ej. "2026-05-12").
        reminder_minutes_before: minutos antes del evento. Default 14400 = 10 días.
        timezone: zona horaria. Default America/Guayaquil.
        categories: lista de categorías Outlook (opcional, ej. ["Pagos"]).

    Returns:
        dict con el evento creado (incluye 'id', 'webLink').
    """
    year, month, day = (int(x) for x in start_date_iso.split("-"))
    return _create_recurring_all_day_event(
        user_email=user_email,
        subject=subject,
        body_html=body_html,
        start_date_iso=start_date_iso,
        recurrence_pattern={
            "type": "absoluteYearly",
            "interval": 1,
            "dayOfMonth": day,
            "month": month,
        },
        reminder_minutes_before=reminder_minutes_before,
        timezone=timezone,
        categories=categories,
        interactive_ok=interactive_ok,
    )


def create_monthly_all_day_event(
    *,
    user_email: str | None,
    subject: str,
    body_html: str,
    start_date_iso: str,
    reminder_minutes_before: int = 4320,  # 3 días por default
    timezone: str = "America/Guayaquil",
    categories: list[str] | None = None,
    interactive_ok: bool = True,
) -> dict:
    """Crea un evento de día completo con recurrencia mensual (mismo día cada mes).

    Default reminder = 3 días antes (4320 min). Cambiar para suscripciones donde
    quieras más anticipación.
    """
    _, _, day = (int(x) for x in start_date_iso.split("-"))
    return _create_recurring_all_day_event(
        user_email=user_email,
        subject=subject,
        body_html=body_html,
        start_date_iso=start_date_iso,
        recurrence_pattern={
            "type": "absoluteMonthly",
            "interval": 1,
            "dayOfMonth": day,
        },
        reminder_minutes_before=reminder_minutes_before,
        timezone=timezone,
        categories=categories,
        interactive_ok=interactive_ok,
    )


def delete_event(
    *,
    user_email: str | None,
    event_id: str,
    interactive_ok: bool = True,
) -> None:
    """Borra un evento por ID. Si es recurrente, borra TODA la serie."""
    if user_email:
        path = f"/users/{user_email}/events/{event_id}"
    else:
        path = f"/me/events/{event_id}"
    _graph_request("DELETE", path, interactive_ok=interactive_ok)


def find_events_by_subject(
    user_email: str | None,
    subject_substring: str,
    *,
    top: int = 100,
    interactive_ok: bool = True,
) -> list[dict]:
    """Busca eventos (serie maestra recurrente) cuyo subject contenga la cadena.

    Útil para no duplicar: antes de crear, ver si ya existe el pago.
    """
    matches = []
    needle = subject_substring.lower()
    for ev in list_calendar_events(user_email, top=top, interactive_ok=interactive_ok):
        if needle in (ev.get("subject") or "").lower():
            matches.append(ev)
    return matches


def _next_day(year: int, month: int, day: int) -> tuple[int, int, int]:
    from datetime import date, timedelta
    nxt = date(year, month, day) + timedelta(days=1)
    return nxt.year, nxt.month, nxt.day


def list_calendar_events(
    user_email: str | None,
    *,
    top: int = 25,
    interactive_ok: bool = True,
) -> list[dict]:
    """Lee eventos del calendario (para verificar que se puede acceder)."""
    if user_email:
        path = f"/users/{user_email}/calendar/events"
    else:
        path = "/me/calendar/events"
    params = {
        "$top": str(top),
        "$select": "id,subject,start,end,isAllDay,recurrence",
        "$orderby": "start/dateTime",
    }
    data = _graph_request("GET", path, params=params, interactive_ok=interactive_ok)
    return data.get("value", [])


if __name__ == "__main__":
    # Smoke test: trae auth y lista 5 eventos del calendario propio.
    print("Autenticando contra Microsoft Graph (Calendars.ReadWrite + .Shared)...")
    get_token(CAL_RW_SCOPES)
    print("OK - token obtenido.\n")
    print("Próximos 5 eventos en tu calendario:")
    for ev in list_calendar_events(None, top=5):
        print(f"  - {ev.get('start', {}).get('dateTime', '?')} → {ev.get('subject')}")
