"""Máquinas de estado VER-OS: ciclo de vida (componente #12) y autonomía (#3).

Reglas:
- Ciclo de vida: solo transiciones declaradas en LIFECYCLE_EDGES.
- Autonomía: promoción de UN nivel a la vez y con evidencia obligatoria;
  demote a cualquier nivel inferior con razón (los downgrades son fáciles a
  propósito — la seguridad no exige papeleo).
El registro en journal/eventos lo hace Department (composición), no esta capa.
"""

from __future__ import annotations

from datetime import datetime, timezone

from org.kernel.manifest import AUTONOMY_LEVELS
from org.kernel.store import TenantStore

LIFECYCLE_EDGES: dict[str, set[str]] = {
    "proposed": {"installed"},
    "installed": {"onboarding"},
    "onboarding": {"active"},
    "active": {"paused", "retiring"},
    "paused": {"active", "retiring"},
    "retiring": {"retired"},
    "retired": set(),
}


class InvalidTransition(ValueError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DeptState:
    def __init__(self, store: TenantStore, dept_id: str, *, autonomy_default: str = "L0"):
        self._store = store
        self._dept_id = dept_id
        if not self._store.query("SELECT 1 FROM dept_state WHERE dept_id = ?", (dept_id,)):
            self._store.execute(
                "INSERT INTO dept_state (dept_id, lifecycle, autonomy, updated_at) VALUES (?, 'proposed', ?, ?)",
                (dept_id, autonomy_default, _now_iso()),
            )

    def _row(self):
        return self._store.query("SELECT * FROM dept_state WHERE dept_id = ?", (self._dept_id,))[0]

    @property
    def lifecycle(self) -> str:
        return self._row()["lifecycle"]

    @property
    def autonomy(self) -> str:
        return self._row()["autonomy"]

    def transition(self, to: str) -> None:
        current = self.lifecycle
        if to not in LIFECYCLE_EDGES:
            raise InvalidTransition(f"estado de ciclo de vida desconocido: {to!r}")
        if to not in LIFECYCLE_EDGES[current]:
            raise InvalidTransition(f"transición de ciclo de vida inválida: {current} → {to}")
        self._store.execute(
            "UPDATE dept_state SET lifecycle = ?, updated_at = ? WHERE dept_id = ?",
            (to, _now_iso(), self._dept_id),
        )

    def promote(self, to: str, evidence: str) -> None:
        if not evidence or not evidence.strip():
            raise ValueError("promover autonomía exige evidencia (criterio medible cumplido)")
        current = self.autonomy
        if to not in AUTONOMY_LEVELS:
            raise InvalidTransition(f"nivel de autonomía desconocido: {to!r}")
        if AUTONOMY_LEVELS.index(to) != AUTONOMY_LEVELS.index(current) + 1:
            raise InvalidTransition(
                f"la autonomía se gana de un nivel a la vez: {current} → {to} no permitido"
            )
        self._store.execute(
            "UPDATE dept_state SET autonomy = ?, updated_at = ? WHERE dept_id = ?",
            (to, _now_iso(), self._dept_id),
        )

    def demote(self, to: str, reason: str) -> None:
        if not reason or not reason.strip():
            raise ValueError("bajar autonomía exige razón registrable")
        current = self.autonomy
        if to not in AUTONOMY_LEVELS:
            raise InvalidTransition(f"nivel de autonomía desconocido: {to!r}")
        if AUTONOMY_LEVELS.index(to) >= AUTONOMY_LEVELS.index(current):
            raise InvalidTransition(f"demote debe bajar el nivel: {current} → {to}")
        self._store.execute(
            "UPDATE dept_state SET autonomy = ?, updated_at = ? WHERE dept_id = ?",
            (to, _now_iso(), self._dept_id),
        )
