"""Puerto de storage del kernel VER-OS (implementación H1: SQLite WAL).

Invariantes que este módulo hace cumplir a nivel de motor, no de disciplina:
- Un archivo de base por tenant → imposible que una query cruce tenants.
- `org_events` y `decision_journal` son append-only vía triggers SQL: ni el
  propio kernel puede UPDATE/DELETE sobre ellas.

En H2 este puerto se reimplementa sobre PostgreSQL con row-level security sin
tocar a los consumidores (todos reciben un TenantStore, no una conexión).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

_TENANT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")

_APPEND_ONLY = ("org_events", "decision_journal", "metering")

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS org_events (
        seq             INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id        TEXT    NOT NULL UNIQUE,
        tenant_id       TEXT    NOT NULL,
        dept_id         TEXT    NOT NULL,
        type            TEXT    NOT NULL,
        schema_version  INTEGER NOT NULL DEFAULT 1,
        occurred_at     TEXT    NOT NULL,
        correlation_id  TEXT,
        payload         TEXT    NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS processed_events (
        consumer  TEXT NOT NULL,
        event_id  TEXT NOT NULL,
        processed_at TEXT NOT NULL,
        PRIMARY KEY (consumer, event_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_journal (
        entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id       TEXT NOT NULL,
        dept_id         TEXT NOT NULL,
        decided_at      TEXT NOT NULL,
        decision        TEXT NOT NULL,
        context_refs    TEXT NOT NULL DEFAULT '[]',
        alternatives    TEXT NOT NULL DEFAULT '[]',
        rule_applied    TEXT,
        correlation_id  TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metering (
        row_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id TEXT NOT NULL,
        dept_id   TEXT NOT NULL,
        unit      TEXT NOT NULL,
        qty       REAL NOT NULL DEFAULT 1.0,
        usd       REAL NOT NULL DEFAULT 0.0,
        month     TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        meta      TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dept_state (
        dept_id    TEXT PRIMARY KEY,
        lifecycle  TEXT NOT NULL DEFAULT 'proposed',
        autonomy   TEXT NOT NULL DEFAULT 'L0',
        updated_at TEXT NOT NULL
    )
    """,
]


def _append_only_triggers() -> list[str]:
    stmts = []
    for table in _APPEND_ONLY:
        for op in ("UPDATE", "DELETE"):
            stmts.append(
                f"""
                CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()}
                BEFORE {op} ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{table} es append-only (invariante VER-OS #4/#7)');
                END
                """
            )
    return stmts


def default_base_dir() -> Path:
    env = os.environ.get("VEROS_DATA_DIR")
    if env:
        return Path(env)
    return Path.home() / ".ver-os"


class TenantStore:
    """Handle de datos de UN tenant. Toda pieza del kernel opera a través de él."""

    def __init__(self, tenant_id: str, base_dir: str | Path | None = None):
        if not _TENANT_RE.match(tenant_id or ""):
            raise ValueError(f"tenant_id inválido: {tenant_id!r} (slug minúsculas)")
        self.tenant_id = tenant_id
        base = Path(base_dir) if base_dir is not None else default_base_dir()
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{tenant_id}.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            for stmt in _DDL + _append_only_triggers():
                self._conn.execute(stmt)
            self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
