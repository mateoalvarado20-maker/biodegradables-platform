"""Estado del reply_agent: qué message_ids ya fueron procesados.

Backend:
- En Azure → Azure Table Storage (`replystate` table), una entidad por msg_id.
- Local → archivo JSON en ~/.claude-agent/reply_state.json (mismo formato
  que la versión local original).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCAL_TZ = timezone(timedelta(hours=-5))

LOCAL_STATE_PATH = Path.home() / ".claude-agent" / "reply_state.json"
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


def is_processed(msg_id: str) -> bool:
    if not msg_id:
        return False
    if _is_azure():
        client = _table_client()
        try:
            client.get_entity(partition_key=PARTITION_KEY, row_key=_safe_key(msg_id))
            return True
        except Exception:
            return False
    # Local fallback
    if not LOCAL_STATE_PATH.exists():
        return False
    try:
        state = json.loads(LOCAL_STATE_PATH.read_text(encoding="utf-8"))
        return msg_id in state.get("processed_message_ids", [])
    except Exception:
        return False


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
    # Local fallback
    LOCAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {"processed_message_ids": []}
    if LOCAL_STATE_PATH.exists():
        try:
            state = json.loads(LOCAL_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    ids = state.setdefault("processed_message_ids", [])
    if msg_id not in ids:
        ids.append(msg_id)
    state["processed_message_ids"] = ids[-500:]  # keep last 500
    LOCAL_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_last_check() -> str | None:
    """Devuelve el ISO timestamp del ultimo check, o None."""
    if _is_azure():
        client = _table_client()
        try:
            ent = client.get_entity(partition_key=PARTITION_KEY, row_key="__meta__last_check")
            return ent.get("ts")
        except Exception:
            return None
    if not LOCAL_STATE_PATH.exists():
        return None
    try:
        state = json.loads(LOCAL_STATE_PATH.read_text(encoding="utf-8"))
        return state.get("last_check_iso")
    except Exception:
        return None


def set_last_check(iso: str) -> None:
    if _is_azure():
        client = _table_client()
        client.upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": "__meta__last_check",
            "ts": iso,
        })
        return
    LOCAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {"last_check_iso": None, "processed_message_ids": []}
    if LOCAL_STATE_PATH.exists():
        try:
            state = json.loads(LOCAL_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    state["last_check_iso"] = iso
    LOCAL_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )
