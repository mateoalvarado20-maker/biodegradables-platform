"""Cola persistente de ContentPackages — F2.0d (ROADMAP.md).

La lección operativa del lote F1.8: los packages vivían en memoria y cada
crash/suspensión perdía el avance. Aquí todo package se persiste con su estado
(draft → copy_approved → produced → qa_approved/qa_rejected → scheduled →
published) en el TenantStore del departamento; matar el proceso a mitad de
lote y relanzar reanuda desde el último estado guardado, sin duplicar ni
perder (los guards de cada etapa — "ya tiene voz/b-roll/render" — hacen el
resto de la idempotencia).

Tabla propia del dominio (`mkt_content_queue`) en el store del tenant —
invariante VER-OS #3: los datos pertenecen a su departamento.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from marketing.models import ContentPackage
from org.kernel.department import Department

_DDL = """
CREATE TABLE IF NOT EXISTS mkt_content_queue (
    package_id  TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    status      TEXT NOT NULL,
    package_json TEXT NOT NULL,
    brief_json  TEXT NOT NULL DEFAULT '{}',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""

# Estados terminales: no se procesan más
TERMINAL = {"qa_rejected", "published"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QueueError(RuntimeError):
    pass


class ContentQueue:
    def __init__(self, dept: Department):
        self._dept = dept
        self._store = dept.storage
        self._store.execute(_DDL)

    def enqueue(self, package: ContentPackage, brief: dict | None = None) -> None:
        if self._store.query(
            "SELECT 1 FROM mkt_content_queue WHERE package_id = ?", (package.package_id,)
        ):
            raise QueueError(f"{package.package_id} ya está en la cola")
        now = _now()
        self._store.execute(
            "INSERT INTO mkt_content_queue (package_id, tenant_id, status, package_json,"
            " brief_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                package.package_id,
                package.tenant_id,
                package.status,
                package.model_dump_json(),
                json.dumps(brief or {}, ensure_ascii=False),
                now,
                now,
            ),
        )

    def save(self, package: ContentPackage) -> None:
        cur = self._store.execute(
            "UPDATE mkt_content_queue SET status = ?, package_json = ?, updated_at = ?,"
            " last_error = NULL WHERE package_id = ?",
            (package.status, package.model_dump_json(), _now(), package.package_id),
        )
        if cur.rowcount != 1:
            raise QueueError(f"{package.package_id} no está en la cola")

    def mark_error(self, package_id: str, error: str) -> None:
        self._store.execute(
            "UPDATE mkt_content_queue SET attempts = attempts + 1, last_error = ?,"
            " updated_at = ? WHERE package_id = ?",
            (error[:2000], _now(), package_id),
        )

    def get(self, package_id: str) -> ContentPackage:
        rows = self._store.query(
            "SELECT package_json FROM mkt_content_queue WHERE package_id = ?", (package_id,)
        )
        if not rows:
            raise QueueError(f"{package_id} no está en la cola")
        return ContentPackage(**json.loads(rows[0]["package_json"]))

    def get_brief(self, package_id: str) -> dict:
        rows = self._store.query(
            "SELECT brief_json FROM mkt_content_queue WHERE package_id = ?", (package_id,)
        )
        if not rows:
            raise QueueError(f"{package_id} no está en la cola")
        return json.loads(rows[0]["brief_json"])

    def ids_with_status(self, *statuses: str) -> list[str]:
        marks = ",".join("?" * len(statuses))
        return [
            r["package_id"]
            for r in self._store.query(
                f"SELECT package_id FROM mkt_content_queue WHERE status IN ({marks})"
                " ORDER BY created_at",
                tuple(statuses),
            )
        ]

    def pending(self) -> list[str]:
        """Todo lo que aún tiene trabajo por delante (no terminal)."""
        marks = ",".join("?" * len(TERMINAL))
        return [
            r["package_id"]
            for r in self._store.query(
                f"SELECT package_id FROM mkt_content_queue WHERE status NOT IN ({marks})"
                " ORDER BY created_at",
                tuple(TERMINAL),
            )
        ]

    def attempts(self, package_id: str) -> tuple[int, str | None]:
        rows = self._store.query(
            "SELECT attempts, last_error FROM mkt_content_queue WHERE package_id = ?",
            (package_id,),
        )
        if not rows:
            raise QueueError(f"{package_id} no está en la cola")
        return rows[0]["attempts"], rows[0]["last_error"]

    def stats(self) -> dict[str, int]:
        return {
            r["status"]: r["n"]
            for r in self._store.query(
                "SELECT status, COUNT(*) AS n FROM mkt_content_queue GROUP BY status"
            )
        }
