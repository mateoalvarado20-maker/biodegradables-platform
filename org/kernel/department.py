"""Department: el chasis VER-OS ensamblado (charter + brain-ports + enforcement).

Composición de los componentes del kernel para UN departamento de UN tenant.
Los agentes de dominio (Estratega, Guionista…) reciben un Department y operan
a través de él; nunca tocan el store directo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from org.kernel.events import Event, EventBus
from org.kernel.journal import DecisionJournal
from org.kernel.manifest import AUTONOMY_LEVELS, Manifest
from org.kernel.metering import Meter
from org.kernel.state import DeptState, InvalidTransition
from org.kernel.store import TenantStore


class CapabilityError(PermissionError):
    pass


@dataclass(frozen=True)
class Charter:
    """Contrato humano↔departamento. Lo aprueba el board; el kernel lo hace cumplir."""

    okrs: tuple[str, ...]
    budget_usd_month: float
    approved_by: str
    approved_at: str
    hard_rules: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.okrs:
            raise ValueError("un charter sin OKRs no es un charter (el depto no tiene contrato)")
        if self.budget_usd_month <= 0:
            raise ValueError("budget_usd_month debe ser > 0")
        if not self.approved_by:
            raise ValueError("approved_by requerido: el board es humano (invariante #5)")


class Department:
    def __init__(
        self,
        manifest: Manifest,
        charter: Charter,
        store: TenantStore,
        *,
        granted_capabilities: set[str] | frozenset[str] = frozenset(),
    ):
        self.manifest = manifest
        self.charter = charter
        self.dept_id = manifest.name
        self._store = store
        # Invariante #2: sin autoridad ambiente. Solo puede otorgarse lo declarado.
        undeclared = set(granted_capabilities) - set(manifest.capabilities)
        if undeclared:
            raise CapabilityError(
                f"capacidades otorgadas pero no declaradas en el manifest: {sorted(undeclared)}"
            )
        self._granted = frozenset(granted_capabilities)

        self.events = EventBus(store)
        self.journal = DecisionJournal(store, self.dept_id)
        self.meter = Meter(store, self.dept_id)
        self.state = DeptState(store, self.dept_id, autonomy_default=manifest.autonomy_default)

    # --- permisos (componente #4) -------------------------------------------
    def ensure_capability(self, name: str) -> None:
        if name not in self._granted:
            raise CapabilityError(
                f"dept {self.dept_id}: capacidad {name!r} no otorgada por el tenant"
            )

    # --- decisión + auditoría (componentes #5) ------------------------------
    def decide(self, decision: str, **kwargs) -> int:
        return self.journal.record(decision, **kwargs)

    # --- eventos (componente #8) --------------------------------------------
    def emit(self, type: str, payload: dict | None = None, **kwargs) -> Event:
        return self.events.emit(self.dept_id, type, payload, **kwargs)

    # --- presupuesto (componente #10, regla dura) ---------------------------
    def ensure_budget(self, about_to_spend_usd: float = 0.0) -> None:
        self.meter.ensure_budget(self.charter.budget_usd_month, about_to_spend_usd)

    # --- ciclo de vida (componente #12) --------------------------------------
    def lifecycle_to(self, to: str) -> None:
        self.state.transition(to)
        self.emit("org.lifecycle_changed", {"to": to})

    # --- autonomía (componente #3) -------------------------------------------
    def promote_autonomy(self, to: str, evidence: str) -> None:
        if AUTONOMY_LEVELS.index(to) > AUTONOMY_LEVELS.index(self.manifest.autonomy_max):
            raise InvalidTransition(
                f"{to} supera el max_level del manifest ({self.manifest.autonomy_max})"
            )
        self.state.promote(to, evidence)
        self.journal.record(f"autonomía promovida a {to}", context_refs=[f"evidencia: {evidence}"])
        self.emit("org.autonomy_changed", {"to": to, "direction": "up"})

    def demote_autonomy(self, to: str, reason: str) -> None:
        self.state.demote(to, reason)
        self.journal.record(f"autonomía degradada a {to}", context_refs=[f"razón: {reason}"])
        self.emit("org.autonomy_changed", {"to": to, "direction": "down"})

    # --- observabilidad (componente #6) --------------------------------------
    def health(self) -> dict:
        last_seq_rows = self._store.query(
            "SELECT MAX(seq) AS m FROM org_events WHERE dept_id = ?", (self.dept_id,)
        )
        last_seq = last_seq_rows[0]["m"] or 0
        last_event = None
        if last_seq:
            row = self._store.query(
                "SELECT type, occurred_at FROM org_events WHERE seq = ?", (last_seq,)
            )[0]
            last_event = {"type": row["type"], "occurred_at": row["occurred_at"]}
        return {
            "tenant_id": self._store.tenant_id,
            "dept_id": self.dept_id,
            "package_version": self.manifest.version,
            "lifecycle": self.state.lifecycle,
            "autonomy": self.state.autonomy,
            "last_event": last_event,
            "month_spend_usd": round(self.meter.month_usd(), 4),
            "budget_usd_month": self.charter.budget_usd_month,
            "journal_entries": len(self.journal.entries(limit=10_000)),
        }
