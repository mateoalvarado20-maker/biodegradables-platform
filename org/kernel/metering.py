"""Metering VER-OS (invariante #7): unidades de trabajo por (tenant, dept, unidad).

El presupuesto del charter es una regla dura: `ensure_budget()` corta (lanza
BudgetExceeded), no advierte. El metering LLM fino ya existe en la plataforma
(`llm_usage.py`); la integración dept↔llm_usage es la tarea F0.10 del ROADMAP.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from org.kernel.store import TenantStore


class BudgetExceeded(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Meter:
    def __init__(self, store: TenantStore, dept_id: str):
        self._store = store
        self._dept_id = dept_id

    def record(
        self,
        unit: str,
        qty: float = 1.0,
        usd: float = 0.0,
        *,
        meta: dict | None = None,
    ) -> None:
        if not unit:
            raise ValueError("unit requerido (p.ej. 'llm_tokens', 'render', 'post')")
        now = _now()
        self._store.execute(
            """
            INSERT INTO metering (tenant_id, dept_id, unit, qty, usd, month, recorded_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._store.tenant_id,
                self._dept_id,
                unit,
                float(qty),
                float(usd),
                now.strftime("%Y-%m"),
                now.isoformat(timespec="seconds"),
                json.dumps(meta or {}, ensure_ascii=False),
            ),
        )

    def month_usd(self, month: str | None = None) -> float:
        month = month or _now().strftime("%Y-%m")
        rows = self._store.query(
            "SELECT COALESCE(SUM(usd), 0.0) AS total FROM metering WHERE dept_id = ? AND month = ?",
            (self._dept_id, month),
        )
        return float(rows[0]["total"])

    def month_units(self, unit: str, month: str | None = None) -> float:
        month = month or _now().strftime("%Y-%m")
        rows = self._store.query(
            "SELECT COALESCE(SUM(qty), 0.0) AS total FROM metering "
            "WHERE dept_id = ? AND month = ? AND unit = ?",
            (self._dept_id, month, unit),
        )
        return float(rows[0]["total"])

    def ensure_budget(self, budget_usd_month: float, about_to_spend_usd: float = 0.0) -> None:
        spent = self.month_usd()
        if spent + about_to_spend_usd > budget_usd_month:
            raise BudgetExceeded(
                f"dept {self._dept_id}: gastado {spent:.2f} + {about_to_spend_usd:.2f} "
                f"supera presupuesto {budget_usd_month:.2f} USD/mes"
            )
