"""Scoring de piezas — F3.2 (ROADMAP.md).

Convierte los snapshots crudos en UN número comparable entre piezas de edades
distintas. Determinista y sin LLM: el aprendizaje se construye sobre
aritmética auditable.

Diseño (cada decisión responde a una pregunta de negocio — regla #16):
- `views` se normaliza por la curva de maduración (una pieza de 12 h no puede
  compararse cruda contra una de 72 h): views_proyectadas = views / madurez.
- El engagement pondera por PODER PREDICTIVO de alcance orgánico: shares (3.0)
  > comments (2.0) > saves (1.5) > likes (1.0) — los shares son la señal más
  predictiva de distribución; los saves aproximan intención de compra.
- follower_delta se pondera aparte (4.0/vista): convertir alcance en audiencia
  propia es el objetivo final del orgánico.
- Sin watch-time (límite de plataforma documentado en metrics.py): la pregunta
  de retención se aproxima con shares+comments.

score = views_proyectadas × (1 + tasa_engagement_ponderada)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

MIN_AGE_HOURS = 12.0  # antes de esto la señal es puro arranque — no se puntúa

# Pesos por OBJETIVO DE NEGOCIO (regla #23): una pieza de conversación no se
# mide como una de awareness. HONESTIDAD sobre leads/sales: hasta que llegue
# el contrato LeadOutcome (conversión real desde Comercial), se aproximan con
# proxies — saves (intención de referencia/compra) y comments (interés activo).
OBJECTIVE_WEIGHTS: dict[str, dict[str, float]] = {
    "awareness": {"shares": 3.0, "comments": 1.5, "saves": 0.5, "likes": 1.0, "follower_delta": 2.0},
    "engagement": {"shares": 2.0, "comments": 4.0, "saves": 1.0, "likes": 2.0, "follower_delta": 1.0},
    "conversations": {"shares": 1.0, "comments": 5.0, "saves": 0.5, "likes": 0.5, "follower_delta": 1.0},
    "leads": {"shares": 1.0, "comments": 2.5, "saves": 3.0, "likes": 0.5, "follower_delta": 2.0},
    "sales": {"shares": 1.0, "comments": 2.0, "saves": 3.5, "likes": 0.5, "follower_delta": 1.5},
    "loyalty": {"shares": 1.0, "comments": 3.0, "saves": 1.0, "likes": 2.0, "follower_delta": 4.0},
    "market_education": {"shares": 2.5, "comments": 1.5, "saves": 3.0, "likes": 0.5, "follower_delta": 1.0},
}


class ScoringError(ValueError):
    pass


def maturity_factor(age_hours: float) -> float:
    """Fracción del alcance total esperado a esta edad (satura ~72 h)."""
    return 1.0 - math.exp(-age_hours / 24.0)


@dataclass(frozen=True)
class PieceScore:
    package_id: str
    score: float
    projected_views: float
    engagement_rate: float
    age_hours: float
    sample_note: str = ""


def score_piece(
    package_id: str,
    latest: dict[str, float],
    age_hours: float,
    objective: str = "awareness",
) -> PieceScore:
    """Score de una pieza desde su último snapshot, ponderado por su OBJETIVO
    de negocio (regla #23). Determinista."""
    if objective not in OBJECTIVE_WEIGHTS:
        raise ScoringError(f"objetivo de negocio desconocido: {objective!r}")
    if age_hours < MIN_AGE_HOURS:
        raise ScoringError(
            f"{package_id}: {age_hours:.0f}h < {MIN_AGE_HOURS:.0f}h — señal demasiado "
            "temprana para puntuar (evita aprender del arranque)"
        )
    views = float(latest.get("views", 0.0))
    if views <= 0:
        raise ScoringError(f"{package_id}: sin views en el snapshot — nada que puntuar")

    projected = views / maturity_factor(age_hours)
    weights = OBJECTIVE_WEIGHTS[objective]
    weighted = sum(w * float(latest.get(name, 0.0)) for name, w in weights.items())
    engagement_rate = weighted / views
    return PieceScore(
        package_id=package_id,
        score=round(projected * (1.0 + engagement_rate), 2),
        projected_views=round(projected, 1),
        engagement_rate=round(engagement_rate, 4),
        age_hours=age_hours,
    )
