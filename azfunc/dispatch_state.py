"""Persistencia del estado de despacho.

Auto-detecta el entorno:
- Si está la env var `AzureWebJobsStorage` (estamos en Azure Functions) → usa
  Azure Table Storage como backend, tabla `dispatchstate`.
- Si no → fallback al archivo local `~/.claude-agent/dispatch_state.json` (mismo
  comportamiento que la versión local original).

Estructura por registro (igual en ambos backends):

    {
        "status": "OK" | "NO" | "PARCIAL",
        "marcado_por": "cli" | "jefe_uio" | "jefe_gye" | "teams:user@dom",
        "marcado_en": "2026-05-21T16:30:00-05:00",
        "razon": ""
    }

Llave: número de factura (campo `documento` de Contifico).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

LOCAL_TZ = timezone(timedelta(hours=-5))

STATE_PATH = Path.home() / ".claude-agent" / "dispatch_state.json"
TABLE_NAME = "dispatchstate"
PARTITION_KEY = "dispatch"

StatusType = Literal["OK", "NO", "PARCIAL"]
VALID_STATUSES: tuple[str, ...] = ("OK", "NO", "PARCIAL")


# ----------- Backend: detección de entorno -----------
def _is_azure() -> bool:
    """True si estamos corriendo en Azure Functions (o cualquier proceso que
    tenga AzureWebJobsStorage configurado)."""
    return bool(os.environ.get("AzureWebJobsStorage"))


def _table_client():
    """Devuelve un TableClient apuntando a la tabla dispatchstate.
    Crea la tabla si no existe. Solo llamar cuando _is_azure() es True."""
    from azure.data.tables import TableServiceClient
    conn_str = os.environ["AzureWebJobsStorage"]
    service = TableServiceClient.from_connection_string(conn_str)
    try:
        service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return service.get_table_client(TABLE_NAME)


# ----------- Sanitización de RowKey (Azure Tables) -----------
# RowKey en Azure Tables NO acepta: / \ # ? \t \n \r ni caracteres de control.
# Los números de factura ("001-002-000008181") son válidos como están, pero
# por seguridad mantenemos un escape.
_INVALID_CHARS = "/\\#?\t\n\r"


def _safe_key(factura: str) -> str:
    out = factura
    for c in _INVALID_CHARS:
        out = out.replace(c, "_")
    return out


# ----------- API pública -----------
def load() -> dict[str, dict[str, Any]]:
    """Devuelve todo el state como dict {factura: {status, ...}}."""
    if _is_azure():
        client = _table_client()
        out: dict[str, dict[str, Any]] = {}
        for ent in client.list_entities():
            factura = ent.get("RowKey") or ""
            out[factura] = {
                "status": ent.get("status", ""),
                "marcado_por": ent.get("marcado_por", ""),
                "marcado_en": ent.get("marcado_en", ""),
                "razon": ent.get("razon", ""),
            }
        return out
    # Local fallback
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(state: dict[str, dict[str, Any]]) -> None:
    """Sobreescribe TODO el state (úsalo con cuidado en Azure — borra el resto).
    Para una sola factura usar mark() en su lugar."""
    if _is_azure():
        client = _table_client()
        # Borrar todo y reescribir es caro y arriesgado; mejor avisar
        raise NotImplementedError(
            "save() en Azure no está soportado — usar mark() por factura"
        )
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def mark(
    factura: str,
    status: StatusType,
    *,
    razon: str = "",
    marcado_por: str = "cli",
) -> dict[str, Any]:
    """Marca una factura con el estado de despacho. Sobreescribe el anterior.
    Devuelve el registro guardado."""
    if status not in VALID_STATUSES:
        raise ValueError(f"Status inválido: {status}.")
    if not factura:
        raise ValueError("factura no puede estar vacío.")
    entry = {
        "status": status,
        "marcado_por": marcado_por,
        "marcado_en": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "razon": razon or "",
    }
    if _is_azure():
        client = _table_client()
        client.upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": _safe_key(factura),
            **entry,
        })
        return entry
    # Local fallback
    state = load()
    state[factura] = entry
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return entry


def clear(factura: str) -> bool:
    """Borra la marca de una factura. Devuelve True si existía."""
    if _is_azure():
        client = _table_client()
        try:
            client.delete_entity(
                partition_key=PARTITION_KEY, row_key=_safe_key(factura)
            )
            return True
        except Exception:
            return False
    state = load()
    if factura not in state:
        return False
    del state[factura]
    save(state)
    return True


def get(factura: str) -> dict[str, Any] | None:
    """Devuelve el registro de una factura o None si no está marcada."""
    if _is_azure():
        client = _table_client()
        try:
            ent = client.get_entity(
                partition_key=PARTITION_KEY, row_key=_safe_key(factura)
            )
            return {
                "status": ent.get("status", ""),
                "marcado_por": ent.get("marcado_por", ""),
                "marcado_en": ent.get("marcado_en", ""),
                "razon": ent.get("razon", ""),
            }
        except Exception:
            return None
    return load().get(factura)


def is_ok(factura: str) -> bool:
    """True solo si la factura está marcada como OK (despachado)."""
    rec = get(factura)
    return bool(rec and rec.get("status") == "OK")


def pendientes_anteriores(
    facturas: list[str], *, dias_minimos: int = 1
) -> list[str]:
    """Filtra la lista dejando solo las que NO están marcadas OK."""
    state = load()
    out = []
    for f in facturas:
        rec = state.get(f)
        if rec is None or rec.get("status") != "OK":
            out.append(f)
    return out


if __name__ == "__main__":
    import sys
    print(f"Azure mode: {_is_azure()}")
    state = load()
    print(f"Total registros: {len(state)}")
    if state:
        print(json.dumps(state, indent=2, ensure_ascii=False))
