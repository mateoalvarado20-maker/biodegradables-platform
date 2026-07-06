"""Decision journal VER-OS (invariante #4: toda acción autónoma es auditable).

Append-only por triggers SQL (ver store.py). Registra QUÉ se decidió, con qué
contexto (referencias, no copias), qué alternativas se consideraron y qué regla
del playbook se aplicó. No expone update/delete — no existen.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from org.kernel.store import TenantStore


class DecisionJournal:
    def __init__(self, store: TenantStore, dept_id: str):
        self._store = store
        self._dept_id = dept_id

    def record(
        self,
        decision: str,
        *,
        context_refs: list[str] | None = None,
        alternatives: list[str] | None = None,
        rule_applied: str | None = None,
        correlation_id: str | None = None,
    ) -> int:
        if not decision or not decision.strip():
            raise ValueError("una decisión vacía no es auditable")
        cur = self._store.execute(
            """
            INSERT INTO decision_journal
                (tenant_id, dept_id, decided_at, decision, context_refs,
                 alternatives, rule_applied, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._store.tenant_id,
                self._dept_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                decision.strip(),
                json.dumps(list(context_refs or []), ensure_ascii=False),
                json.dumps(list(alternatives or []), ensure_ascii=False),
                rule_applied,
                correlation_id,
            ),
        )
        return cur.lastrowid

    def entries(self, limit: int = 100) -> list[dict]:
        rows = self._store.query(
            "SELECT * FROM decision_journal WHERE dept_id = ? ORDER BY entry_id DESC LIMIT ?",
            (self._dept_id, limit),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["context_refs"] = json.loads(d["context_refs"])
            d["alternatives"] = json.loads(d["alternatives"])
            out.append(d)
        return out
