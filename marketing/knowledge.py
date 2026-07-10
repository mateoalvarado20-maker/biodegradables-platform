"""Knowledge Manager — F3.4 (regla #20 del board).

El ÚNICO componente que escribe el playbook. Su política es DETERMINISTA y
auditable (umbrales explícitos, no un LLM opinando):

- Propuesta "crear" con confianza media/alta → regla **experimental**.
- Promoción por madurez (la decide el KM leyendo el historial del registro):
  experimental → validada con ≥2 confirmaciones consecutivas;
  validada → consolidada con ≥4 confirmaciones consecutivas.
  Nunca se salta niveles (regla #17: conservador).
- Propuesta "obsoletar" (contradicción con confianza media/alta):
  experimental → obsoleta directa; validada/consolidada → BAJA UN nivel
  (una regla probada durante meses no muere por una mala evaluación) —
  salvo contradicción con confianza alta y sin confusores, que obsoleta
  a las validadas.
- Propuesta con confianza baja o campos incompletos → rechazada.

Toda decisión (aceptada o no) queda en el journal con la propuesta completa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from marketing.analista import ChangeProposal
from marketing.experiments import ExperimentRegistry
from marketing.playbook import Playbook
from org.kernel.department import Department

logger = logging.getLogger("marketing.knowledge")

VERSION = "0.1"
DECIDER = f"knowledge-manager@{VERSION}"
PROMOTE_TO_VALIDADA = 2  # confirmaciones consecutivas
PROMOTE_TO_CONSOLIDADA = 4


@dataclass(frozen=True)
class Decision:
    proposal: ChangeProposal | None
    accepted: bool
    action_taken: str
    rule_id: str
    rationale: str


class KnowledgeManager:
    def __init__(self, dept: Department, playbook: Playbook, registry: ExperimentRegistry):
        self._dept = dept
        self._playbook = playbook
        self._registry = registry

    def _journal(self, decision: Decision) -> None:
        p = decision.proposal
        refs = [f"decisión: {decision.action_taken}", f"razón: {decision.rationale}"]
        if p is not None:
            refs += [
                f"evidencia a favor: {p.evidence_for[0] if p.evidence_for else '-'}",
                f"evidencia en contra: {p.evidence_against[0] if p.evidence_against else 'ninguna'}",
                f"riesgo de aceptar: {p.risks_accept}",
                f"riesgo de no aceptar: {p.risks_reject}",
                f"impacto esperado: {p.expected_impact}",
                f"confianza: {p.confidence}",
                f"reversibilidad: {p.reversibility}",
            ]
        self._dept.decide(
            f"knowledge-manager {'ACEPTA' if decision.accepted else 'RECHAZA'}: "
            f"{decision.action_taken} ({decision.rule_id})",
            context_refs=refs[:10],
            correlation_id=decision.rule_id,
        )

    def consider(self, proposal: ChangeProposal) -> Decision:
        """Decide sobre una propuesta del Analista."""
        key = f"{proposal.dimension}={proposal.value}"
        rule_id = f"regla:{key}"

        # los 8 campos son obligatorios (regla #20)
        obligatorios = (
            proposal.evidence_for, proposal.risks_accept, proposal.risks_reject,
            proposal.expected_impact, proposal.confidence, proposal.reversibility,
        )
        if not all(obligatorios):
            decision = Decision(proposal, False, "rechazo por propuesta incompleta", rule_id,
                                "faltan campos obligatorios de la regla #20")
            self._journal(decision)
            return decision
        if proposal.confidence == "baja":
            decision = Decision(proposal, False, "rechazo por confianza insuficiente", rule_id,
                                "el conocimiento permanente exige confianza media o alta (regla #17)")
            self._journal(decision)
            return decision

        existing = self._playbook.get(rule_id)

        if proposal.kind == "crear":
            if existing and existing["status"] != "obsoleta":
                decision = Decision(proposal, False, "sin cambio (la regla ya existe)", rule_id,
                                    f"estado actual: {existing['status']}")
                self._journal(decision)
                return decision
            self._playbook.write_revision(
                rule_id,
                status="experimental",
                action=proposal.proposed_action,
                dimension=proposal.dimension,
                value=proposal.value,
                proposed_by=proposal.proposed_by,
                decided_by=DECIDER,
                rationale=(
                    f"creada como EXPERIMENTAL — impacto esperado {proposal.expected_impact}; "
                    f"riesgo asumido: {proposal.risks_accept}"
                ),
                evidence=proposal.evidence_for,
            )
            decision = Decision(proposal, True, "regla creada como experimental", rule_id,
                                "una observación no modifica el comportamiento global: "
                                "nace experimental y madura con evidencia (regla #20)")
            self._journal(decision)
            return decision

        # obsoletar / degradar
        if not existing or existing["status"] == "obsoleta":
            decision = Decision(proposal, False, "sin cambio (no hay regla activa)", rule_id,
                                "nada que obsoletar")
            self._journal(decision)
            return decision
        status = existing["status"]
        strong = proposal.confidence == "alta" and not (
            proposal.basis and proposal.basis.confounders
        )
        if status == "experimental" or (status == "validada" and strong):
            nuevo = "obsoleta"
        else:
            orden = ["experimental", "validada", "consolidada"]
            nuevo = orden[orden.index(status) - 1]
        self._playbook.write_revision(
            rule_id,
            status=nuevo,  # type: ignore[arg-type]
            action=existing["action"],
            dimension=proposal.dimension,
            value=proposal.value,
            proposed_by=proposal.proposed_by,
            decided_by=DECIDER,
            rationale=(
                f"degradada {status}→{nuevo} por contradicción "
                f"(confianza {proposal.confidence}): {proposal.risks_reject}"
            ),
            evidence=proposal.evidence_for,
            impact_notes=f"contradicha tras estado {status}",
        )
        decision = Decision(proposal, True, f"regla degradada {status}→{nuevo}", rule_id,
                            "las reglas maduras bajan de a un nivel; las experimentales "
                            "mueren directo (regla #20)")
        self._journal(decision)
        return decision

    def review_promotions(self) -> list[Decision]:
        """Promociones por madurez: SOLO con confirmaciones consecutivas en el
        historial del registro (el Analista no interviene)."""
        out: list[Decision] = []
        for rule_id, rule in self._playbook.rules().items():
            key = f"{rule['dimension']}={rule['value']}"
            hist = self._registry.history(key)
            streak = 0
            for h in reversed(hist):
                if h["verdict"] == "confirmada":
                    streak += 1
                else:
                    break
            target = None
            if rule["status"] == "experimental" and streak >= PROMOTE_TO_VALIDADA:
                target = "validada"
            elif rule["status"] == "validada" and streak >= PROMOTE_TO_CONSOLIDADA:
                target = "consolidada"
            if target:
                self._playbook.write_revision(
                    rule_id,
                    status=target,  # type: ignore[arg-type]
                    action=rule["action"],
                    dimension=rule["dimension"],
                    value=rule["value"],
                    proposed_by=rule["proposed_by"],
                    decided_by=DECIDER,
                    rationale=(
                        f"promovida {rule['status']}→{target}: {streak} confirmaciones "
                        "consecutivas en el registro de experimentos"
                    ),
                    evidence=[f"racha de {streak} confirmadas para {key}"],
                    impact_notes=rule["impact_notes"],
                )
                d = Decision(None, True, f"promovida {rule['status']}→{target}", rule_id,
                             f"madurez ganada con {streak} confirmaciones consecutivas")
                self._journal(d)
                out.append(d)
        return out
