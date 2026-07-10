"""Métricas con propósito — F3.1 (ROADMAP.md, regla #16 del board).

Toda métrica almacenada responde una pregunta de negocio. El mapa `PURPOSES`
es el CONTRATO: un campo que no esté ahí no se puede almacenar (lo valida el
código, no la disciplina). Agregar un campo nuevo exige escribir su pregunta —
o cuestionar si vale la pena guardarlo.

El puerto `MetricsSource` tiene dos implementaciones: el simulador con sesgos
sembrados (aquí — ground truth para validar el Analista en F3.4) y, en la fase
de integración TikTok diferida, el adapter real de la Display API (mismo
puerto, cero cambios en el motor).
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from marketing.models import ContentPackage
from org.kernel.department import Department

# ===== El contrato dato→decisión (regla #16) =====
PURPOSES: dict[str, str] = {
    "views": "¿Qué pilar/gancho/horario genera mayor alcance?",
    "likes": "¿Qué contenido resuena con la audiencia (aprobación pasiva)?",
    "comments": "¿Qué contenido abre conversación (señal fuerte del algoritmo)?",
    "shares": "¿Qué formato/gancho genera viralidad (la señal más predictiva de alcance orgánico)?",
    "saves": "¿Qué contenido tiene valor de referencia (checklists/educativo → intención de compra)?",
    "follower_delta": "¿Qué piezas convierten alcance en audiencia propia?",
}
# NO almacenable hoy (documentado, no olvidado): watch_time/retención — la
# Display API de TikTok no lo expone (PROPUESTA §3). La pregunta '¿qué estilo
# retiene?' se aproxima con shares+comments hasta que la plataforma lo permita.


class MetricsError(ValueError):
    pass


@dataclass(frozen=True)
class PostMetrics:
    captured_at: str
    age_hours: float
    values: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        unknown = set(self.values) - set(PURPOSES)
        if unknown:
            raise MetricsError(
                f"métricas sin pregunta de negocio asociada (regla #16): {sorted(unknown)} — "
                "agrega su propósito a PURPOSES o cuestiona si vale la pena almacenarlas"
            )
        if self.age_hours < 0:
            raise MetricsError("age_hours no puede ser negativo")


# (package, age_hours) -> PostMetrics
MetricsSource = Callable[[ContentPackage, float], PostMetrics]

_DDL = """
CREATE TABLE IF NOT EXISTS mkt_metrics_snapshots (
    row_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id  TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    age_hours   REAL NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL
)
"""


class MetricsStore:
    """Serie temporal de snapshots por package, en el store del departamento."""

    def __init__(self, dept: Department):
        self._dept = dept
        self._store = dept.storage
        self._store.execute(_DDL)

    def record(self, package_id: str, metrics: PostMetrics) -> None:
        for name, value in metrics.values.items():
            self._store.execute(
                "INSERT INTO mkt_metrics_snapshots"
                " (package_id, captured_at, age_hours, metric, value) VALUES (?, ?, ?, ?, ?)",
                (package_id, metrics.captured_at, metrics.age_hours, name, float(value)),
            )

    def series(self, package_id: str, metric: str) -> list[tuple[float, float]]:
        if metric not in PURPOSES:
            raise MetricsError(f"métrica desconocida: {metric!r}")
        return [
            (r["age_hours"], r["value"])
            for r in self._store.query(
                "SELECT age_hours, value FROM mkt_metrics_snapshots"
                " WHERE package_id = ? AND metric = ? ORDER BY age_hours",
                (package_id, metric),
            )
        ]

    def latest(self, package_id: str) -> dict[str, float]:
        rows = self._store.query(
            "SELECT metric, value FROM mkt_metrics_snapshots WHERE package_id = ?"
            " AND age_hours = (SELECT MAX(age_hours) FROM mkt_metrics_snapshots"
            "                  WHERE package_id = ?)",
            (package_id, package_id),
        )
        return {r["metric"]: r["value"] for r in rows}

    def packages_with_metrics(self) -> list[str]:
        return [
            r["package_id"]
            for r in self._store.query(
                "SELECT DISTINCT package_id FROM mkt_metrics_snapshots ORDER BY package_id"
            )
        ]


# ===== Simulador con sesgos sembrados (ground truth del Analista, F3.4) =====

_BASE_RATES = {"likes": 0.06, "comments": 0.008, "shares": 0.012, "saves": 0.02}
_BASE_VIEWS = 900.0


class BiasedSimulator:
    """MetricsSource sintético con sesgos CONOCIDOS.

    `biases` mapea (dimensión, valor) → multiplicador, p. ej.
    {("hook_type", "pregunta"): 1.8} significa que las piezas con ese gancho
    rinden 1.8× en views/shares. El Analista (F3.4) debe DESCUBRIR estos
    sesgos a partir de los datos — y no inventar sesgos donde el multiplicador
    es 1.0 (control negativo). Determinista por package (sin reloj ni RNG
    global): mismo package + misma edad → mismas métricas.
    """

    def __init__(self, biases: dict[tuple[str, str], float] | None = None, noise: float = 0.15):
        self.biases = dict(biases or {})
        self.noise = noise

    def _multiplier(self, package: ContentPackage) -> float:
        dims = {
            "pillar": package.labels.pillar,
            "hook_type": package.labels.hook_type,
            "format": package.labels.format,
            "time_slot": package.labels.time_slot,
            "cta_type": package.labels.cta_type,
        }
        mult = 1.0
        for (dim, value), factor in self.biases.items():
            if dims.get(dim) == value:
                mult *= factor
        return mult

    def __call__(self, package: ContentPackage, age_hours: float) -> PostMetrics:
        seed = zlib.crc32(package.package_id.encode("utf-8"))
        rng = random.Random(seed)
        jitter = 1.0 + rng.uniform(-self.noise, self.noise)
        # curva de maduración: ~63% del alcance a las 24h, satura a las ~72h
        maturity = 1.0 - math.exp(-age_hours / 24.0)
        views = _BASE_VIEWS * self._multiplier(package) * jitter * maturity
        values = {"views": round(views, 1)}
        for name, rate in _BASE_RATES.items():
            r_jitter = 1.0 + rng.uniform(-self.noise, self.noise)
            values[name] = round(views * rate * r_jitter, 1)
        values["follower_delta"] = round(values["shares"] * 0.35, 1)
        return PostMetrics(
            captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            age_hours=age_hours,
            values=values,
        )
