"""Bus de eventos VER-OS (invariante #6: envelope estándar, append-only, idempotencia).

El bus es por tenant (vive en su TenantStore). Los consumidores reclaman cada
evento con `process(consumer, event_id)`: la primera vez devuelve True (procesa),
las siguientes False — así un job re-lanzado tras un crash no duplica side-effects.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from org.kernel.store import TenantStore


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Event:
    seq: int
    event_id: str
    tenant_id: str
    dept_id: str
    type: str
    schema_version: int
    occurred_at: str
    correlation_id: str | None
    payload: dict


class EventBus:
    def __init__(self, store: TenantStore):
        self._store = store

    def emit(
        self,
        dept_id: str,
        type: str,
        payload: dict | None = None,
        *,
        correlation_id: str | None = None,
        schema_version: int = 1,
    ) -> Event:
        if not type or "." not in type:
            raise ValueError(f"type de evento inválido: {type!r} (formato 'dominio.accion')")
        event_id = uuid.uuid4().hex
        occurred_at = _now_iso()
        cur = self._store.execute(
            """
            INSERT INTO org_events
                (event_id, tenant_id, dept_id, type, schema_version,
                 occurred_at, correlation_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                self._store.tenant_id,
                dept_id,
                type,
                schema_version,
                occurred_at,
                correlation_id,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
        return Event(
            seq=cur.lastrowid,
            event_id=event_id,
            tenant_id=self._store.tenant_id,
            dept_id=dept_id,
            type=type,
            schema_version=schema_version,
            occurred_at=occurred_at,
            correlation_id=correlation_id,
            payload=payload or {},
        )

    def fetch(
        self,
        after_seq: int = 0,
        *,
        types: list[str] | None = None,
        limit: int = 100,
    ) -> list[Event]:
        sql = "SELECT * FROM org_events WHERE seq > ?"
        params: list = [after_seq]
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            params.extend(types)
        sql += " ORDER BY seq LIMIT ?"
        params.append(limit)
        return [self._row_to_event(r) for r in self._store.query(sql, tuple(params))]

    def process(self, consumer: str, event_id: str) -> bool:
        """Reclama un evento para un consumidor. True = procesar; False = ya visto."""
        try:
            self._store.execute(
                "INSERT INTO processed_events (consumer, event_id, processed_at) VALUES (?, ?, ?)",
                (consumer, event_id, _now_iso()),
            )
            return True
        except Exception:
            already = self._store.query(
                "SELECT 1 FROM processed_events WHERE consumer = ? AND event_id = ?",
                (consumer, event_id),
            )
            if already:
                return False
            raise

    @staticmethod
    def _row_to_event(row) -> Event:
        return Event(
            seq=row["seq"],
            event_id=row["event_id"],
            tenant_id=row["tenant_id"],
            dept_id=row["dept_id"],
            type=row["type"],
            schema_version=row["schema_version"],
            occurred_at=row["occurred_at"],
            correlation_id=row["correlation_id"],
            payload=json.loads(row["payload"]),
        )
