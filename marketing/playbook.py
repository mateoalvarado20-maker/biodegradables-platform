"""Playbook versionado — el conocimiento del departamento (regla #20).

Cada regla tiene MADUREZ (experimental → validada → consolidada → obsoleta) y
su historial completo vive en una tabla append-only de revisiones: el estado
actual de una regla es su ÚLTIMA revisión, y "revertir" = escribir una revisión
nueva que copia una anterior — el historial jamás se pierde. Esto responde las
preguntas del board a 6 meses vista: quién propuso, con qué evidencia, qué la
validó, cuándo/por qué cambió, qué impacto produjo.

SOLO el Knowledge Manager escribe aquí (regla #20). El Analista propone; el
Planificador lee `active_rules()`. Hay un test de capas que verifica que el
módulo del Analista ni siquiera importa este archivo.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Literal

from org.kernel.department import Department

RuleStatus = Literal["experimental", "validada", "consolidada", "obsoleta"]
STATUS_ORDER = ("experimental", "validada", "consolidada")  # obsoleta = fuera

# Peso de la madurez para el Planificador (una hipótesis nueva no pesa igual
# que una regla probada durante meses)
STATUS_WEIGHT = {"experimental": 0.5, "validada": 1.0, "consolidada": 1.5}

_DDL = """
CREATE TABLE IF NOT EXISTS mkt_playbook_revisions (
    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id     TEXT NOT NULL,
    revision    INTEGER NOT NULL,
    status      TEXT NOT NULL,
    action      TEXT NOT NULL,
    dimension   TEXT NOT NULL,
    value       TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    decided_by  TEXT NOT NULL,
    rationale   TEXT NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '[]',
    impact_notes TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
)
"""


class PlaybookError(RuntimeError):
    pass


class Playbook:
    def __init__(self, dept: Department):
        self._dept = dept
        self._store = dept.storage
        self._store.execute(_DDL)

    # --- lectura (cualquiera) ------------------------------------------------

    def rules(self, include_obsolete: bool = False) -> dict[str, dict]:
        """Estado actual: última revisión por rule_id."""
        rows = self._store.query(
            "SELECT * FROM mkt_playbook_revisions ORDER BY revision_id"
        )
        latest: dict[str, dict] = {}
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            latest[d["rule_id"]] = d
        if include_obsolete:
            return latest
        return {k: v for k, v in latest.items() if v["status"] != "obsoleta"}

    def get(self, rule_id: str) -> dict | None:
        return self.rules(include_obsolete=True).get(rule_id)

    def history(self, rule_id: str) -> list[dict]:
        rows = self._store.query(
            "SELECT * FROM mkt_playbook_revisions WHERE rule_id = ? ORDER BY revision",
            (rule_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["evidence"] = json.loads(d["evidence"])
            out.append(d)
        return out

    def active_rules(self) -> list[dict]:
        """Para el Planificador: reglas vigentes con su peso por madurez."""
        return [
            {**r, "weight": STATUS_WEIGHT[r["status"]]}
            for r in self.rules().values()
        ]

    # --- escritura (SOLO Knowledge Manager) ----------------------------------

    def write_revision(
        self,
        rule_id: str,
        *,
        status: RuleStatus,
        action: str,
        dimension: str,
        value: str,
        proposed_by: str,
        decided_by: str,
        rationale: str,
        evidence: list[str],
        impact_notes: str = "",
    ) -> int:
        """API de escritura del Knowledge Manager. Append-only por revisiones."""
        prev = self.get(rule_id)
        revision = (prev["revision"] + 1) if prev else 1
        self._store.execute(
            "INSERT INTO mkt_playbook_revisions"
            " (rule_id, revision, status, action, dimension, value, proposed_by,"
            "  decided_by, rationale, evidence, impact_notes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id,
                revision,
                status,
                action,
                dimension,
                value,
                proposed_by,
                decided_by,
                rationale,
                json.dumps(list(evidence), ensure_ascii=False),
                impact_notes,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self._dept.emit(
            "playbook.rule_changed",
            {"rule_id": rule_id, "revision": revision, "status": status,
             "rationale": rationale[:200]},
            correlation_id=rule_id,
        )
        return revision

    def revert(self, rule_id: str, to_revision: int, *, reason: str, decided_by: str) -> int:
        """Volver EXACTAMENTE a una revisión anterior sin perder historial:
        se escribe una revisión nueva que copia la antigua."""
        hist = self.history(rule_id)
        target = next((h for h in hist if h["revision"] == to_revision), None)
        if target is None:
            raise PlaybookError(f"{rule_id}: no existe la revisión {to_revision}")
        return self.write_revision(
            rule_id,
            status=target["status"],
            action=target["action"],
            dimension=target["dimension"],
            value=target["value"],
            proposed_by=target["proposed_by"],
            decided_by=decided_by,
            rationale=f"REVERT a revisión {to_revision}: {reason}",
            evidence=target["evidence"],
            impact_notes=target["impact_notes"],
        )
