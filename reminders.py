"""Persistencia de recordatorios programados (Phase E + G.1).

Permite a gerencia (Daniel + Gabriela via Data Bot) programar mensajes que el
bot le entrega a colaboradores en una fecha/hora específica. Soporta
**recurrencia** (Phase G.1): un reminder con `recurrence` se reprograma
automáticamente después de entregarse.

State file: `~/.claude-agent/reminders.json`. Estructura:

    {
        "reminders": [
            {
                "id": "abc123",
                "target_user": "malvarado@biodegradablesecuador.com",
                "send_at": "2026-06-14T08:00:00-05:00",
                "message": "Reunión semanal de equipo a las 10",
                "created_by": "dsanchez@biodegradablesecuador.com",
                "created_at": "2026-05-30T14:00:00-05:00",
                "sent": false,
                "sent_at": null,
                "recurrence": "weekly_mon"     // opcional
            }
        ]
    }

Recurrencias soportadas:
- "daily"           — todos los días a la misma hora
- "weekly"          — mismo día de la semana cada semana
- "weekdays"        — lun-vie
- "monthly"         — mismo día del mes cada mes (skips si no existe ese día)
- "weekly_mon" .. "weekly_sun" — lunes, martes, etc. específicos
- None / vacío      — one-shot (no se reprograma)

El scheduler entrega vencidos cada 5 min. Si el reminder tiene recurrence,
después de entregarlo crea uno NUEVO con send_at = próxima ocurrencia.
"""
from __future__ import annotations

import functools
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

LOCAL_TZ = timezone(timedelta(hours=-5))
import os as _os
STATE_PATH = Path(_os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "reminders.json"

# Fase 1: lock por archivo — el ciclo load→mutar→save de cada mutadora es
# atómico dentro del proceso (el job deliver_reminders y la creación desde
# worker threads del Data Bot se serializan; auditoría H6/H13).
_LOCK = safe_json.lock_for(STATE_PATH)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def load() -> dict[str, Any]:
    # safe_json: atómico + backup + cuarentena (auditoría H2).
    return safe_json.load_json(STATE_PATH, lambda: {"reminders": []})


def save(state: dict[str, Any]) -> None:
    safe_json.save_json(STATE_PATH, state)


VALID_RECURRENCES = (
    "", "daily", "weekly", "weekdays", "monthly",
    "weekly_mon", "weekly_tue", "weekly_wed", "weekly_thu",
    "weekly_fri", "weekly_sat", "weekly_sun",
)

WEEKLY_DAY_TO_INDEX = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


@_locked
def add_reminder(
    target_user: str,
    send_at_iso: str,
    message: str,
    *,
    created_by: str = "",
    recurrence: str = "",
) -> dict[str, Any]:
    """Agrega un recordatorio nuevo.

    Args:
        target_user: email del colaborador que recibirá el mensaje.
        send_at_iso: fecha/hora en ISO. Si no tiene timezone, asume EC (UTC-5).
        message: contenido del recordatorio.
        created_by: email de quien lo programó (Daniel, Gabriela).
        recurrence: '' (one-shot), 'daily', 'weekly', 'weekdays', 'monthly',
                    'weekly_mon'..'weekly_sun'.
    """
    if recurrence and recurrence not in VALID_RECURRENCES:
        raise ValueError(f"recurrence inválida: {recurrence}. Opciones: {VALID_RECURRENCES}")

    state = load()
    rid = uuid.uuid4().hex[:12]
    rec = {
        "id": rid,
        "target_user": target_user.strip().lower(),
        "send_at": send_at_iso,
        "message": message,
        "created_by": created_by.strip().lower(),
        "created_at": _now_iso(),
        "sent": False,
        "sent_at": None,
        "recurrence": recurrence or "",
    }
    state["reminders"].append(rec)
    save(state)
    return rec


def _next_occurrence(current: datetime, recurrence: str) -> datetime | None:
    """Calcula el próximo send_at según el tipo de recurrencia.

    Devuelve None si recurrence está vacío o es inválido (one-shot).
    """
    if not recurrence:
        return None
    if recurrence == "daily":
        return current + timedelta(days=1)
    if recurrence == "weekly":
        return current + timedelta(weeks=1)
    if recurrence == "weekdays":
        # lun-vie: si current es viernes, next = próximo lunes
        nxt = current + timedelta(days=1)
        while nxt.weekday() >= 5:  # 5=sab, 6=dom
            nxt += timedelta(days=1)
        return nxt
    if recurrence == "monthly":
        # Mismo día del próximo mes (clamp al último día si no existe)
        import calendar
        next_month = current.month + 1
        next_year = current.year
        if next_month > 12:
            next_month = 1
            next_year += 1
        last_day = calendar.monthrange(next_year, next_month)[1]
        day = min(current.day, last_day)
        return current.replace(year=next_year, month=next_month, day=day)
    if recurrence.startswith("weekly_"):
        suffix = recurrence.split("_", 1)[1]
        target_idx = WEEKLY_DAY_TO_INDEX.get(suffix)
        if target_idx is None:
            return None
        # Próximo "ese día de la semana" después de current
        days_ahead = (target_idx - current.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        return current + timedelta(days=days_ahead)
    return None


def reschedule_recurring(reminder: dict[str, Any]) -> dict[str, Any] | None:
    """Crea el siguiente reminder en la serie si tiene recurrence.

    Llamado por el scheduler después de marcar un reminder como sent.
    Devuelve el nuevo reminder o None si no era recurrente.
    """
    recurrence = reminder.get("recurrence", "")
    if not recurrence:
        return None
    try:
        current = datetime.fromisoformat(reminder["send_at"])
        if current.tzinfo is None:
            current = current.replace(tzinfo=LOCAL_TZ)
    except (ValueError, KeyError, TypeError):
        return None
    next_dt = _next_occurrence(current, recurrence)
    if next_dt is None:
        return None
    return add_reminder(
        reminder["target_user"],
        next_dt.isoformat(timespec="seconds"),
        reminder["message"],
        created_by=reminder.get("created_by", ""),
        recurrence=recurrence,
    )


def list_reminders(
    target_user: str | None = None, only_pending: bool = True
) -> list[dict[str, Any]]:
    state = load()
    out = []
    for r in state.get("reminders", []):
        if only_pending and r.get("sent"):
            continue
        if target_user and r["target_user"].lower() != target_user.lower():
            continue
        out.append(r)
    return out


def get_due_reminders() -> list[dict[str, Any]]:
    """Devuelve reminders con send_at <= now y aún no marcados como sent."""
    now = _now()
    state = load()
    due = []
    for r in state.get("reminders", []):
        if r.get("sent"):
            continue
        try:
            send_at = datetime.fromisoformat(r["send_at"])
            if send_at.tzinfo is None:
                send_at = send_at.replace(tzinfo=LOCAL_TZ)
            if send_at <= now:
                due.append(r)
        except (ValueError, KeyError, TypeError):
            pass
    return due


@_locked
def mark_sent(reminder_id: str) -> bool:
    state = load()
    for r in state.get("reminders", []):
        if r["id"] == reminder_id:
            r["sent"] = True
            r["sent_at"] = _now_iso()
            save(state)
            return True
    return False


@_locked
def cancel_reminder(reminder_id: str) -> bool:
    """Borra un recordatorio que todavía no se envió."""
    state = load()
    before = len(state.get("reminders", []))
    state["reminders"] = [
        r for r in state.get("reminders", [])
        if not (r["id"] == reminder_id and not r.get("sent"))
    ]
    if len(state["reminders"]) < before:
        save(state)
        return True
    return False


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"State file: {STATE_PATH}")
    state = load()
    n = len(state.get("reminders", []))
    print(f"Total reminders: {n}")
    if n:
        print(json.dumps(state, indent=2, ensure_ascii=False))
