"""Registro de experimentos — F3.3 (ROADMAP.md, reglas #17 y #19 del board).

El veredicto de una hipótesis dimensional ("las piezas con hook_type=pregunta
rinden mejor que el resto") lo computa ESTADÍSTICA CONSERVADORA (t de Welch,
sin dependencias nuevas), no un LLM: "no sé" es un resultado calculado, no una
pose. Cuatro veredictos (regla #19):

- confirmada        — efecto positivo con |t| suficiente
- rechazada         — efecto negativo con |t| suficiente, o efecto ~nulo con
                      muestra adecuada ("no hay diferencia" también es hallazgo)
- inconclusa        — muestra adecuada pero no se puede separar el efecto del
                      ruido
- requiere_mas_datos — muestra insuficiente para decir nada

Toda conclusión lleva (regla #19): confianza, tamaño de muestra, evidencia
(package_ids y medias), factores de confusión detectados, y qué datos
adicionales subirían la confianza. El historial por hipótesis es append-only:
es la base del KPI Learning Accuracy (¿las confirmadas sobreviven a más datos?).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from marketing.models import ContentPackage
from marketing.scoring import PieceScore
from org.kernel.department import Department

Verdict = Literal["confirmada", "rechazada", "inconclusa", "requiere_mas_datos"]
Confidence = Literal["baja", "media", "alta"]

MIN_N_PER_GROUP = 5  # regla #17: nada de conclusiones con 3 piezas
T_MEDIA = 2.0  # |t| para confianza media (~p<0.05 con n moderado)
T_ALTA = 3.0  # |t| para confianza alta
NEGLIGIBLE_EFFECT = 0.10  # ±10%: por debajo, la diferencia no es accionable

DIMENSIONS = ("pillar", "hook_type", "format", "time_slot", "cta_type")


@dataclass(frozen=True)
class ExperimentConclusion:
    hypothesis_key: str  # p.ej. "hook_type=pregunta"
    hypothesis: str
    verdict: Verdict
    confidence: Confidence
    sample_size: dict  # {"grupo": n, "resto": m}
    effect: float  # (media_grupo / media_resto) - 1
    evidence: list[str] = field(default_factory=list)
    confounders: list[str] = field(default_factory=list)
    next_data_needed: str = ""


def _welch_t(a: list[float], b: list[float]) -> float:
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / max(len(a) - 1, 1)
    vb = sum((x - mb) ** 2 for x in b) / max(len(b) - 1, 1)
    denom = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / denom if denom > 0 else 0.0


def _find_confounders(
    group: list[ContentPackage], rest: list[ContentPackage], tested_dim: str
) -> list[str]:
    """Dimensión no testeada cuya distribución difiere fuerte entre grupos →
    posible factor de confusión (el efecto podría venir de ahí)."""
    out = []
    for dim in DIMENSIONS:
        if dim == tested_dim:
            continue
        vals_g = {getattr(p.labels, dim if dim != "pillar" else "pillar") for p in group}
        vals_r = {getattr(p.labels, dim if dim != "pillar" else "pillar") for p in rest}
        if len(vals_g) == 1 and vals_g != vals_r and len(vals_r) > 0:
            out.append(
                f"todas las piezas del grupo comparten {dim}={next(iter(vals_g))!r} — "
                "el efecto podría venir de esa dimensión, no de la testeada"
            )
    return out


def evaluate_hypothesis(
    dimension: str,
    value: str,
    scored: list[tuple[ContentPackage, PieceScore]],
) -> ExperimentConclusion:
    """Evalúa "las piezas con <dimension>=<value> rinden mejor que el resto"."""
    if dimension not in DIMENSIONS:
        raise ValueError(f"dimensión desconocida: {dimension!r}")
    group = [(p, s) for p, s in scored if getattr(p.labels, dimension) == value]
    rest = [(p, s) for p, s in scored if getattr(p.labels, dimension) != value]
    key = f"{dimension}={value}"
    hypothesis = f"las piezas con {key} rinden mejor que el resto"
    n_g, n_r = len(group), len(rest)
    sample = {"grupo": n_g, "resto": n_r}

    if n_g < MIN_N_PER_GROUP or n_r < MIN_N_PER_GROUP:
        return ExperimentConclusion(
            hypothesis_key=key,
            hypothesis=hypothesis,
            verdict="requiere_mas_datos",
            confidence="baja",
            sample_size=sample,
            effect=0.0,
            evidence=[f"grupo n={n_g}, resto n={n_r}"],
            next_data_needed=(
                f"al menos {MIN_N_PER_GROUP} piezas por grupo "
                f"(faltan {max(0, MIN_N_PER_GROUP - n_g)} del grupo y "
                f"{max(0, MIN_N_PER_GROUP - n_r)} del resto)"
            ),
        )

    scores_g = [s.score for _, s in group]
    scores_r = [s.score for _, s in rest]
    mean_g, mean_r = sum(scores_g) / n_g, sum(scores_r) / n_r
    effect = (mean_g / mean_r) - 1.0 if mean_r > 0 else 0.0
    t = _welch_t(scores_g, scores_r)
    confounders = _find_confounders([p for p, _ in group], [p for p, _ in rest], dimension)
    evidence = [
        f"media grupo={mean_g:.1f} (n={n_g}) vs resto={mean_r:.1f} (n={n_r})",
        f"efecto={effect:+.1%}, t_welch={t:.2f}",
    ] + [f"pieza grupo: {p.package_id}" for p, _ in group[:8]]

    if abs(t) >= T_MEDIA:
        confidence: Confidence = "alta" if abs(t) >= T_ALTA else "media"
        if confounders and confidence == "alta":
            confidence = "media"  # un confusor detectado baja la confianza
        verdict: Verdict = "confirmada" if t > 0 else "rechazada"
        next_needed = (
            "más piezas variando las dimensiones de confusión detectadas"
            if confounders
            else f"replicar con piezas nuevas (n≥{n_g + 5}) para subir la confianza"
        )
    elif abs(effect) <= NEGLIGIBLE_EFFECT and n_g + n_r >= 3 * MIN_N_PER_GROUP:
        verdict, confidence = "rechazada", "media"
        next_needed = "n/a — el efecto es despreciable con muestra adecuada"
    elif n_g + n_r >= 3 * MIN_N_PER_GROUP:
        verdict, confidence = "inconclusa", "baja"
        next_needed = (
            f"no se puede diferenciar el efecto ({effect:+.1%}) del ruido con esta "
            f"muestra; ~{2 * (n_g + n_r)} piezas darían poder estadístico"
        )
    else:
        verdict, confidence = "requiere_mas_datos", "baja"
        next_needed = f"muestra total {n_g + n_r} < {3 * MIN_N_PER_GROUP} para separar señal de ruido"

    return ExperimentConclusion(
        hypothesis_key=key,
        hypothesis=hypothesis,
        verdict=verdict,
        confidence=confidence,
        sample_size=sample,
        effect=round(effect, 4),
        evidence=evidence,
        confounders=confounders,
        next_data_needed=next_needed,
    )


_DDL = """
CREATE TABLE IF NOT EXISTS mkt_experiments (
    row_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_key TEXT NOT NULL,
    verdict        TEXT NOT NULL,
    confidence     TEXT NOT NULL,
    effect         REAL NOT NULL,
    payload        TEXT NOT NULL,
    evaluated_at   TEXT NOT NULL
)
"""


class ExperimentRegistry:
    """Historial append-only de evaluaciones por hipótesis (base del KPI LA)."""

    def __init__(self, dept: Department):
        self._dept = dept
        self._store = dept.storage
        self._store.execute(_DDL)

    def record(self, conclusion: ExperimentConclusion) -> None:
        self._store.execute(
            "INSERT INTO mkt_experiments"
            " (hypothesis_key, verdict, confidence, effect, payload, evaluated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                conclusion.hypothesis_key,
                conclusion.verdict,
                conclusion.confidence,
                conclusion.effect,
                json.dumps(conclusion.__dict__, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        self._dept.decide(
            f"experimento {conclusion.hypothesis_key}: {conclusion.verdict} "
            f"(confianza {conclusion.confidence}, efecto {conclusion.effect:+.1%}, "
            f"n={conclusion.sample_size})",
            context_refs=(
                conclusion.evidence[:3]
                + [f"confusor: {c}" for c in conclusion.confounders]
                + [f"datos que faltan: {conclusion.next_data_needed}"]
            ),
            correlation_id=conclusion.hypothesis_key,
        )

    def history(self, hypothesis_key: str) -> list[dict]:
        return [
            json.loads(r["payload"])
            for r in self._store.query(
                "SELECT payload FROM mkt_experiments WHERE hypothesis_key = ?"
                " ORDER BY row_id",
                (hypothesis_key,),
            )
        ]

    def latest_verdicts(self) -> dict[str, dict]:
        rows = self._store.query(
            "SELECT hypothesis_key, payload FROM mkt_experiments ORDER BY row_id"
        )
        return {r["hypothesis_key"]: json.loads(r["payload"]) for r in rows}
