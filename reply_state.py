"""Estado del reply_agent: qué message_ids ya fueron procesados.

Fase 4 (2026-06-12): módulo ÚNICO para ambos runtimes (antes vivía solo en
azfunc/ y la copia raíz tenía su propia implementación inline — auditoría
P3: dos universos de estado).

Backend:
- En Azure (env `AzureWebJobsStorage`) → Azure Table Storage (`replystate`),
  una entidad por msg_id — atómico por fila, seguro ante concurrencia.
- Local → ~/.claude-agent/reply_state.json vía safe_json (atómico + backup
  + cuarentena, Fase 1).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import safe_json

LOCAL_TZ = timezone(timedelta(hours=-5))

LOCAL_STATE_PATH = (
    Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent"))
    / "reply_state.json"
)
TABLE_NAME = "replystate"
PARTITION_KEY = "reply"

# Caracteres invalidos en RowKey de Azure Tables
_INVALID_CHARS = "/\\#?\t\n\r"


def _is_azure() -> bool:
    return bool(os.environ.get("AzureWebJobsStorage"))


def _table_client():
    from azure.data.tables import TableServiceClient
    conn_str = os.environ["AzureWebJobsStorage"]
    service = TableServiceClient.from_connection_string(conn_str)
    try:
        service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return service.get_table_client(TABLE_NAME)


def _safe_key(msg_id: str) -> str:
    out = msg_id
    for c in _INVALID_CHARS:
        out = out.replace(c, "_")
    # Tables RowKey max 1024 chars
    return out[:1024]


def _default() -> dict:
    return {"last_check_iso": None, "processed_message_ids": []}


def is_processed(msg_id: str) -> bool:
    if not msg_id:
        return False
    if _is_azure():
        client = _table_client()
        try:
            client.get_entity(partition_key=PARTITION_KEY, row_key=_safe_key(msg_id))
            return True
        except Exception:
            # OJO: un fallo de red/credenciales de Table se reporta como
            # "no procesado" — preferimos un posible draft duplicado a
            # perder un prospecto. Queda logueado por el caller.
            return False
    state = safe_json.load_json(LOCAL_STATE_PATH, _default)
    return msg_id in state.get("processed_message_ids", [])


def mark_processed(msg_id: str) -> None:
    if not msg_id:
        return
    if _is_azure():
        client = _table_client()
        client.upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": _safe_key(msg_id),
            "processed_at": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        })
        return

    def mutate(state: dict) -> None:
        ids = state.setdefault("processed_message_ids", [])
        if msg_id not in ids:
            ids.append(msg_id)
        state["processed_message_ids"] = ids[-500:]  # keep last 500

    safe_json.locked_update(LOCAL_STATE_PATH, _default, mutate)


def get_last_check() -> str | None:
    """Devuelve el ISO timestamp del ultimo check, o None."""
    if _is_azure():
        client = _table_client()
        try:
            ent = client.get_entity(partition_key=PARTITION_KEY, row_key="__meta__last_check")
            return ent.get("ts")
        except Exception:
            return None
    state = safe_json.load_json(LOCAL_STATE_PATH, _default)
    return state.get("last_check_iso")


def set_last_check(iso: str) -> None:
    if _is_azure():
        client = _table_client()
        client.upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": "__meta__last_check",
            "ts": iso,
        })
        return

    def mutate(state: dict) -> None:
        state["last_check_iso"] = iso

    safe_json.locked_update(LOCAL_STATE_PATH, _default, mutate)
