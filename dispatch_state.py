"""Persistencia del estado de despacho.

State file: `~/.claude-agent/dispatch_state.json`. Estructura:

    {
        "001-002-000008181": {
            "status": "OK" | "NO" | "PARCIAL",
            "marcado_por": "cli" | "jefe_uio" | "jefe_gye" | "teams:user@dom",
            "marcado_en": "2026-05-21T16:30:00-05:00",
            "razon": ""
        },
        ...
    }

Llaves = número de factura (campo `documento` de Contifico).

Compartido entre Fase 1 (CLI `dispatch.py`) y Fase 2 (bot de Teams). Diseñado
para ser idempotente: marcar dos veces el mismo pedido sobreescribe el estado
anterior con timestamp nuevo.
"""
from __future__ import annotations

import functools
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import safe_json

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador (UTC-5)

STATE_PATH = Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "dispatch_state.json"

StatusType = Literal["OK", "NO", "PARCIAL"]
VALID_STATUSES: tuple[str, ...] = ("OK", "NO", "PARCIAL")

# Fase 1: atómico + lock (auditoría H1/H2).
_LOCK = safe_json.lock_for(STATE_PATH)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def load() -> dict[str, dict[str, Any]]:
    """Lee el state file completo. Devuelve {} si no existe."""
    return safe_json.load_json(STATE_PATH, dict)


def save(state: dict[str, dict[str, Any]]) -> None:
    """Persiste el state file (atómico, con backup)."""
    safe_json.save_json(STATE_PATH, state, sort_keys=True)


@_locked
def mark(
    factura: str,
    status: StatusType,
    *,
    razon: str = "",
    marcado_por: str = "cli",
) -> dict[str, Any]:
    """Marca una factura con el estado de despacho. Sobreescribe el anterior.

    Devuelve el registro guardado.
    """
    if status not in VALID_STATUSES:
        raise ValueError(f"Status inválido: {status}. Debe ser uno de {VALID_STATUSES}.")
    if not factura:
        raise ValueError("factura no puede estar vacío.")
    state = load()
    entry = {
        "status": status,
        "marcado_por": marcado_por,
        "marcado_en": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "razon": razon or "",
    }
    state[factura] = entry
    save(state)
    return entry


@_locked
def clear(factura: str) -> bool:
    """Borra la marca de una factura. Devuelve True si existía."""
    state = load()
    if factura not in state:
        return False
    del state[factura]
    save(state)
    return True


def get(factura: str) -> dict[str, Any] | None:
    """Devuelve el registro de una factura o None si no está marcada."""
    return load().get(factura)


def is_ok(factura: str) -> bool:
    """True solo si la factura está marcada como OK (despachado)."""
    rec = get(factura)
    return bool(rec and rec.get("status") == "OK")


def pendientes_anteriores(
    facturas: list[str], *, dias_minimos: int = 1
) -> list[str]:
    """Filtra la lista de facturas dejando solo las que NO están marcadas OK.

    `dias_minimos` está reservado para futura lógica de antigüedad si se quiere
    filtrar por edad de la factura (requiere fecha de emisión).
    """
    state = load()
    out = []
    for f in facturas:
        rec = state.get(f)
        if rec is None or rec.get("status") != "OK":
            out.append(f)
    return out


if __name__ == "__main__":
    # Smoke test
    import sys
    print(f"State file: {STATE_PATH}")
    state = load()
    print(f"Total registros: {len(state)}")
    if state:
        print(json.dumps(state, indent=2, ensure_ascii=False))
