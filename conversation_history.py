"""Per-user conversation history para multi-turn chat con el bot.

State file: `~/.claude-agent/conversation_history.json`. Estructura:

    {
        "<email>:<mode>": {
            "history": [
                {"role": "user", "content": "Quiero añadir una actividad"},
                {"role": "assistant", "content": "Dale, ¿qué actividad?"},
                ...
            ],
            "last_ts": "2026-06-01T18:30:00-05:00"
        }
    }

Llave compuesta `email:mode` — cada user tiene history SEPARADA por bot
(data vs activities), porque son conversaciones de naturaleza distinta.

Reglas:
- Solo guardamos texto natural (slash commands y card submissions NO van).
- Máximo MAX_TURNS turns (last user+assistant pairs).
- TTL 30 min — si pasó más tiempo desde el último turn, arrancamos limpio.
  Esto evita que un context viejo confunda una pregunta nueva.

Phase J (2026-06-01).
"""
from __future__ import annotations

import functools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

LOCAL_TZ = timezone(timedelta(hours=-5))
import os as _os
STATE_PATH = Path(_os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "conversation_history.json"
TTL_MINUTES = 30
MAX_TURNS = 12  # ~6 exchanges (user + assistant cada uno)

# Fase 1: atómico + lock (auditoría H1/H2).
_LOCK = safe_json.lock_for(STATE_PATH)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _key(user_email: str, mode: str) -> str:
    return f"{user_email.strip().lower()}:{mode}"


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def load() -> dict[str, Any]:
    return safe_json.load_json(STATE_PATH, dict)


def save(state: dict[str, Any]) -> None:
    safe_json.save_json(STATE_PATH, state)


def get_history(user_email: str, mode: str) -> list[dict[str, Any]]:
    """Devuelve la history reciente para user+mode, o [] si expiró o no hay."""
    if not user_email:
        return []
    state = load()
    entry = state.get(_key(user_email, mode), {})
    last_ts = entry.get("last_ts")
    if not last_ts:
        return []
    try:
        last = datetime.fromisoformat(last_ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=LOCAL_TZ)
        if _now() - last > timedelta(minutes=TTL_MINUTES):
            return []  # expirado
    except (ValueError, TypeError):
        return []
    return entry.get("history", [])


@_locked
def add_turns(
    user_email: str,
    mode: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """Agrega un par (user turn, assistant turn) al history.

    Trunca a MAX_TURNS si crece. Actualiza last_ts.
    """
    if not user_email or not user_message or not assistant_message:
        return
    state = load()
    key = _key(user_email, mode)
    entry = state.get(key, {"history": [], "last_ts": None})
    entry["history"].append({"role": "user", "content": user_message})
    entry["history"].append({"role": "assistant", "content": assistant_message})
    entry["history"] = entry["history"][-MAX_TURNS:]
    entry["last_ts"] = _now().isoformat(timespec="seconds")
    state[key] = entry
    save(state)


@_locked
def clear_history(user_email: str, mode: str) -> bool:
    """Borra la history de un user+mode. Devuelve True si existía."""
    if not user_email:
        return False
    state = load()
    key = _key(user_email, mode)
    if key in state:
        del state[key]
        save(state)
        return True
    return False


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    state = load()
    print(f"State path: {STATE_PATH}")
    print(f"Conversaciones activas: {len(state)}")
    for key, entry in state.items():
        turns = len(entry.get("history", []))
        last = entry.get("last_ts", "?")
        print(f"  {key}: {turns} turns, last_ts={last}")
