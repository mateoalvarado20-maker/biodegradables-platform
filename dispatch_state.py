"""Persistencia del estado de despacho — módulo ÚNICO para ambos runtimes.

Fase 4 (2026-06-12): unifica las dos copias divergentes (raíz solo-archivo
vs azfunc con Table Storage). Auditoría P1 (split-brain): lo que Mateo o
Gabriela marcaban con el CLI local iba a un JSON que el reporte de logística
en Azure (que lee Table Storage) JAMÁS veía — los badges salían "pendiente"
eternamente.

Backend, en orden de prioridad:
1. `DISPATCH_TABLE_CONN` (connection string explícita — para que el CLI
   local escriba a la MISMA tabla de producción).
2. `AzureWebJobsStorage` (presente en Azure Functions / App Service).
3. Archivo local `~/.claude-agent/dispatch_state.json` vía safe_json
   (atómico + backup + cuarentena, Fase 1).

Estructura por registro (igual en todos los backends):

    {
        "status": "OK" | "NO" | "PARCIAL",
        "marcado_por": "cli" | "jefe_uio" | "jefe_gye" | "teams:user@dom",
        "marcado_en": "2026-05-21T16:30:00-05:00",
        "razon": ""
    }

Llave: número de factura (campo `documento` de Contifico).
"""
from __future__ import annotations

import functools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import safe_json

logger = logging.getLogger("dispatch_state")

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador (UTC-5)

STATE_PATH = Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "dispatch_state.json"
TABLE_NAME = "dispatchstate"
PARTITION_KEY = "dispatch"

StatusType = Literal["OK", "NO", "PARCIAL"]
VALID_STATUSES: tuple[str, ...] = ("OK", "NO", "PARCIAL")

_LOCK = safe_json.lock_for(STATE_PATH)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


# ----------- Backend: detección de entorno -----------
def _conn_str() -> str:
    return (
        os.environ.get("DISPATCH_TABLE_CONN", "").strip()
        or os.environ.get("AzureWebJobsStorage", "").strip()
    )


def _is_table() -> bool:
    return bool(_conn_str())


def _table_client():
    """TableClient de la tabla dispatchstate (la crea si no existe)."""
    from azure.data.tables import TableServiceClient
    service = TableServiceClient.from_connection_string(_conn_str())
    try:
        service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return service.get_table_client(TABLE_NAME)


# ----------- Sanitización de RowKey (Azure Tables) -----------
_INVALID_CHARS = "/\\#?\t\n\r"


def _safe_key(factura: str) -> str:
    out = factura
    for c in _INVALID_CHARS:
        out = out.replace(c, "_")
    return out


# ----------- API pública -----------
def load() -> dict[str, dict[str, Any]]:
    """Devuelve todo el state como dict {factura: {status, ...}}."""
    if _is_table():
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
    return safe_json.load_json(STATE_PATH, dict)


def save(state: dict[str, dict[str, Any]]) -> None:
    """Sobreescribe TODO el state. Solo backend archivo — en Table usar
    mark() por factura (reescribir todo es caro y arriesgado)."""
    if _is_table():
        raise NotImplementedError(
            "save() con backend Table no está soportado — usar mark() por factura"
        )
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
    entry = {
        "status": status,
        "marcado_por": marcado_por,
        "marcado_en": datetime.now(LOCAL_TZ).isoformat(timespec="seconds"),
        "razon": razon or "",
    }
    if _is_table():
        client = _table_client()
        client.upsert_entity({
            "PartitionKey": PARTITION_KEY,
            "RowKey": _safe_key(factura),
            **entry,
        })
        return entry
    state = load()
    state[factura] = entry
    save(state)
    return entry


@_locked
def clear(factura: str) -> bool:
    """Borra la marca de una factura. Devuelve True si existía."""
    if _is_table():
        client = _table_client()
        try:
            client.delete_entity(
                partition_key=PARTITION_KEY, row_key=_safe_key(factura)
            )
            return True
        except Exception as e:
            logger.warning("clear(%s) en Table falló: %s", factura, e)
            return False
    state = load()
    if factura not in state:
        return False
    del state[factura]
    save(state)
    return True


def get(factura: str) -> dict[str, Any] | None:
    """Devuelve el registro de una factura o None si no está marcada."""
    if _is_table():
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
    print(f"Backend: {'Azure Table' if _is_table() else f'archivo {STATE_PATH}'}")
    state = load()
    print(f"Total registros: {len(state)}")
    if state:
        print(json.dumps(state, indent=2, ensure_ascii=False))
