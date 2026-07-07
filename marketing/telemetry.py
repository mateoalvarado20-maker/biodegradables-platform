"""Telemetría de eficiencia del pipeline (directriz #14 del board).

Cada etapa del pipeline se envuelve en `stage()`: al salir registra en el
meter del departamento una fila `stage_ms` con el tiempo transcurrido y el
meta que la etapa haya llenado (tokens, reuso de caché, duración de la pieza).
Consultable con `stage_stats()` — y en F4, en el dashboard.
"""

from __future__ import annotations

import time
from contextlib import contextmanager

from org.kernel.department import Department

UNIT = "stage_ms"


@contextmanager
def stage(dept: Department, package_id: str, name: str):
    """`with stage(dept, pkg_id, "tts") as info:` — la etapa puede llenar
    `info` (tokens, cached, reused…) y queda todo en una fila del meter."""
    info: dict = {}
    t0 = time.perf_counter()
    try:
        yield info
    finally:
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
        dept.meter.record(
            UNIT,
            qty=elapsed_ms,
            usd=0.0,
            meta={"stage": name, "package": package_id, **info},
        )


def stage_stats(dept: Department, month: str | None = None) -> dict:
    """Resumen por etapa del mes: corridas, ms totales/promedio y meta agregada
    numérica (tokens, reused…). Base del dashboard de eficiencia (F4)."""
    rows = dept.meter.month_rows(UNIT, month)
    out: dict[str, dict] = {}
    for r in rows:
        meta = r["meta"]
        name = meta.get("stage", "desconocida")
        s = out.setdefault(name, {"runs": 0, "total_ms": 0.0})
        s["runs"] += 1
        s["total_ms"] += r["qty"]
        for k, v in meta.items():
            if k in ("stage", "package"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                s[k] = round(s.get(k, 0) + v, 3)
    for s in out.values():
        s["avg_ms"] = round(s["total_ms"] / s["runs"], 1)
        s["total_ms"] = round(s["total_ms"], 1)
    return out
