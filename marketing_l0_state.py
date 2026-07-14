"""Store de decisiones L0 de Marketing — compartido bot ↔ PC (M1, 2026-07-14).

El pipeline de Marketing corre en la PC (SQLite local) y el bot corre en el
App Service: no comparten disco. Este módulo es el punto de encuentro para la
tarjeta de aprobación L0 en Teams:

    PC (fin de corrida) ──POST /admin/marketing/l0-cards──▶ bot
        bot: create_pending() + tarjeta proactiva a los aprobadores
    Daniel toca Aprobar/Rechazar en Teams
        bot: record_decision()
    PC (inicio de la siguiente corrida, o CLI `aplicar`)
        ──GET /admin/marketing/l0-decisions──▶ aplica approve()/reject()
        ──POST /admin/marketing/l0-applied──▶ mark_applied()

Backend (mismo patrón que dispatch_state.py):
1. `MARKETING_L0_TABLE_CONN` (connection string explícita).
2. `AzureWebJobsStorage` (presente en el App Service).
3. Archivo local `~/.claude-agent/marketing_l0_state.json` (tests/dev).

La PC NO necesita el connection string: habla solo HTTP con el admin API.

Registro por pieza (RowKey = package_id):
    titulo, formato, resumen, deciders (csv de emails autorizados),
    creado_en, decision ("" | aprobar | rechazar), decided_by, decided_at,
    motivo, applied_at ("" = la PC aún no la aplicó).
"""
from __future__ import annotations

import functools
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

logger = logging.getLogger("marketing_l0_state")

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador (UTC-5)

STATE_PATH = Path(
    os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")
) / "marketing_l0_state.json"
TABLE_NAME = "marketingl0"
PARTITION_KEY = "l0"

VALID_DECISIONS = ("aprobar", "rechazar")

_LOCK = safe_json.lock_for(STATE_PATH)

_FIELDS = (
    "titulo", "formato", "resumen", "deciders", "creado_en",
    "decision", "decided_by", "decided_at", "motivo", "applied_at",
)


class L0StateError(RuntimeError):
    """Transición inválida (pieza inexistente, ya decidida, sin motivo…)."""


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _now() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


# ----------- Backend -----------
def _conn_str() -> str:
    return (
        os.environ.get("MARKETING_L0_TABLE_CONN", "").strip()
        or os.environ.get("AzureWebJobsStorage", "").strip()
    )


def _is_table() -> bool:
    return bool(_conn_str())


def _table_client():
    from azure.data.tables import TableServiceClient
    service = TableServiceClient.from_connection_string(_conn_str())
    try:
        service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return service.get_table_client(TABLE_NAME)


def _entity_to_entry(ent: dict) -> dict[str, Any]:
    return {f: ent.get(f, "") for f in _FIELDS}


def _get_raw(package_id: str) -> dict[str, Any] | None:
    if _is_table():
        try:
            ent = _table_client().get_entity(
                partition_key=PARTITION_KEY, row_key=package_id
            )
            return _entity_to_entry(ent)
        except Exception:
            return None
    return safe_json.load_json(STATE_PATH, dict).get(package_id)


def _put(package_id: str, entry: dict[str, Any]) -> None:
    if _is_table():
        _table_client().upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": package_id,
            **entry,
        })
        return
    state = safe_json.load_json(STATE_PATH, dict)
    state[package_id] = entry
    safe_json.save_json(STATE_PATH, state, sort_keys=True)


def _all() -> dict[str, dict[str, Any]]:
    if _is_table():
        return {
            ent.get("RowKey", ""): _entity_to_entry(ent)
            for ent in _table_client().list_entities()
        }
    return safe_json.load_json(STATE_PATH, dict)


# ----------- API pública -----------
def get(package_id: str) -> dict[str, Any] | None:
    return _get_raw(package_id)


@_locked
def create_pending(
    package_id: str,
    *,
    titulo: str,
    formato: str,
    deciders: list[str],
    resumen: str = "",
) -> dict[str, Any]:
    """Registra una pieza esperando decisión. Idempotente: si ya existe,
    NO pisa una decisión tomada — devuelve el registro tal cual está."""
    if not package_id:
        raise L0StateError("package_id vacío")
    if not deciders:
        raise L0StateError("deciders vacío — nadie podría aprobar la pieza")
    existing = _get_raw(package_id)
    if existing is not None:
        return existing
    entry = {
        "titulo": (titulo or "")[:200],
        "formato": (formato or "")[:40],
        "resumen": (resumen or "")[:500],
        "deciders": ",".join(d.strip().lower() for d in deciders if d.strip()),
        "creado_en": _now(),
        "decision": "",
        "decided_by": "",
        "decided_at": "",
        "motivo": "",
        "applied_at": "",
    }
    _put(package_id, entry)
    return entry


def is_decider(entry: dict[str, Any], email: str) -> bool:
    allowed = {e for e in (entry.get("deciders") or "").split(",") if e}
    return (email or "").strip().lower() in allowed


@_locked
def record_decision(
    package_id: str,
    decision: str,
    *,
    decided_by: str,
    motivo: str = "",
) -> dict[str, Any]:
    """Registra la decisión humana. Falla claro si la pieza no espera decisión,
    si ya fue decidida (anti doble-tap) o si el rechazo viene sin motivo."""
    if decision not in VALID_DECISIONS:
        raise L0StateError(f"decisión inválida: {decision!r}")
    if decision == "rechazar" and not motivo.strip():
        raise L0StateError("rechazar exige motivo (auditoría L0)")
    entry = _get_raw(package_id)
    if entry is None:
        raise L0StateError(f"{package_id} no está esperando aprobación")
    if entry.get("decision"):
        raise L0StateError(
            f"{package_id} ya fue {entry['decision']} por {entry['decided_by']} "
            f"el {entry['decided_at']}"
        )
    if not is_decider(entry, decided_by):
        raise L0StateError(f"{decided_by} no está autorizado a decidir {package_id}")
    entry.update(
        decision=decision,
        decided_by=(decided_by or "").strip().lower(),
        decided_at=_now(),
        motivo=(motivo or "").strip()[:300],
    )
    _put(package_id, entry)
    return entry


def unapplied_decisions() -> dict[str, dict[str, Any]]:
    """Decididas en Teams pero aún no aplicadas por la PC."""
    return {
        pid: e for pid, e in _all().items()
        if e.get("decision") and not e.get("applied_at")
    }


def pending() -> dict[str, dict[str, Any]]:
    """Sin decisión todavía (para re-enviar tarjetas / debug)."""
    return {pid: e for pid, e in _all().items() if not e.get("decision")}


@_locked
def mark_applied(package_id: str) -> None:
    entry = _get_raw(package_id)
    if entry is None:
        raise L0StateError(f"{package_id} no existe en el store L0")
    entry["applied_at"] = _now()
    _put(package_id, entry)


if __name__ == "__main__":
    import json
    print(f"Backend: {'Azure Table' if _is_table() else f'archivo {STATE_PATH}'}")
    print(json.dumps(_all(), indent=2, ensure_ascii=False))
