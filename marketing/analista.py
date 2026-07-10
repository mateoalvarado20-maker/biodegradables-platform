"""Analista — F3.4 (reglas #17, #19 y #20 del board).

El Analista OBSERVA (scores), FORMULA hipótesis dimensionales, EVALÚA evidencia
(experiments.py, estadística conservadora) y PROPONE cambios de conocimiento.
JAMÁS escribe el playbook — ni siquiera lo importa (hay test de capas que lo
verifica). Sus propuestas van al Knowledge Manager con los 8 campos
obligatorios de la regla #20.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from marketing.experiments import (
    DIMENSIONS,
    MIN_N_PER_GROUP,
    ExperimentConclusion,
    ExperimentRegistry,
    evaluate_hypothesis,
)
from marketing.models import ContentPackage
from marketing.scoring import PieceScore
from org.kernel.department import Department

VERSION = "0.1"
PROPOSER = f"analista@{VERSION}"


@dataclass(frozen=True)
class ChangeProposal:
    """Propuesta de cambio de conocimiento — los 8 campos de la regla #20."""

    target_knowledge: str  # rule_id existente o "NUEVA: <key>"
    proposed_action: str  # qué regla crear / degradar
    kind: str  # "crear" | "obsoletar"
    dimension: str
    value: str
    evidence_for: list[str]
    evidence_against: list[str]
    risks_accept: str
    risks_reject: str
    expected_impact: str
    confidence: str
    reversibility: str
    proposed_by: str = PROPOSER
    basis: ExperimentConclusion | None = field(default=None, compare=False)


def _candidate_values(
    scored: list[tuple[ContentPackage, PieceScore]], dimension: str
) -> list[str]:
    counts: dict[str, int] = {}
    for p, _ in scored:
        v = getattr(p.labels, dimension)
        counts[v] = counts.get(v, 0) + 1
    # solo valores con muestra mínima Y con "resto" suficiente
    total = len(scored)
    return [v for v, n in counts.items() if n >= MIN_N_PER_GROUP and total - n >= MIN_N_PER_GROUP]


def _proposal_from(
    conclusion: ExperimentConclusion,
    kind: str,
    target: str,
    history: list[dict],
) -> ChangeProposal:
    dim, value = conclusion.hypothesis_key.split("=", 1)
    against = [
        f"evaluación previa ({h['verdict']}, confianza {h['confidence']}, efecto {h['effect']:+.1%})"
        for h in history
        if h["verdict"] != conclusion.verdict
    ] + [f"confusor: {c}" for c in conclusion.confounders]
    n = sum(conclusion.sample_size.values())
    if kind == "crear":
        action = f"priorizar {conclusion.hypothesis_key} en la planificación"
        risks_accept = (
            f"sobreajustar a la muestra actual (n={n}); el efecto podría no sostenerse "
            "con briefs nuevos"
        )
        risks_reject = (
            f"seguir planificando sin explotar un efecto medido de {conclusion.effect:+.1%}"
        )
        impact = f"~{conclusion.effect:+.1%} de score en piezas que adopten {conclusion.hypothesis_key}"
    else:  # obsoletar
        action = f"retirar la regla sobre {conclusion.hypothesis_key} (evidencia la contradice)"
        risks_accept = "perder una regla que quizá funcionaba en condiciones no medidas aún"
        risks_reject = (
            f"seguir aplicando conocimiento contradicho por los datos (efecto {conclusion.effect:+.1%})"
        )
        impact = "eliminar una guía contraproducente de la planificación"
    return ChangeProposal(
        target_knowledge=target,
        proposed_action=action,
        kind=kind,
        dimension=dim,
        value=value,
        evidence_for=conclusion.evidence,
        evidence_against=against,
        risks_accept=risks_accept,
        risks_reject=risks_reject,
        expected_impact=impact,
        confidence=conclusion.confidence,
        reversibility="alta — el playbook es versionado por revisiones con revert",
        basis=conclusion,
    )


def run_analysis(
    dept: Department,
    scored: list[tuple[ContentPackage, PieceScore]],
    registry: ExperimentRegistry,
    active_rules: dict[str, dict],
) -> tuple[list[ExperimentConclusion], list[ChangeProposal]]:
    """Un ciclo de análisis. `active_rules` llega como DATO (dict rule_id→regla,
    se lo pasa el orquestador) — el Analista no importa el Playbook (regla #20).

    Devuelve (todas las conclusiones registradas, propuestas para el KM)."""
    conclusions: list[ExperimentConclusion] = []
    proposals: list[ChangeProposal] = []
    for dimension in DIMENSIONS:
        for value in _candidate_values(scored, dimension):
            conclusion = evaluate_hypothesis(dimension, value, scored)
            registry.record(conclusion)
            conclusions.append(conclusion)
            key = conclusion.hypothesis_key
            rule = active_rules.get(f"regla:{key}")
            history = registry.history(key)[:-1]  # evaluaciones previas
            if conclusion.verdict == "confirmada" and conclusion.confidence in ("media", "alta"):
                if rule is None:
                    proposals.append(_proposal_from(conclusion, "crear", f"NUEVA: {key}", history))
                # si la regla ya existe, la promoción por madurez es asunto
                # del Knowledge Manager (lee el historial), no del Analista
            elif (
                conclusion.verdict == "rechazada"
                and conclusion.confidence in ("media", "alta")
                and rule is not None
            ):
                proposals.append(_proposal_from(conclusion, "obsoletar", f"regla:{key}", history))
    dept.decide(
        f"ciclo de análisis: {len(conclusions)} hipótesis evaluadas, "
        f"{len(proposals)} propuestas para el Knowledge Manager",
        context_refs=[f"{c.hypothesis_key}: {c.verdict} ({c.confidence})" for c in conclusions][:10],
        correlation_id="analisis",
    )
    return conclusions, proposals
