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


# --- KPI First Pass Yield (board 2026-07-09; objetivo >80%) ---------------------

_ERROR_CATEGORIES = {
    "emojis": ("emoji",),
    "claims": ("claim", "sustenta", "inventad", "respald"),
    "duración": ("duración", "duracion"),
    "cta": ("cta",),
    "estilo/tono": ("tono", "emoji", "marca"),
}


def _categorize(reason: str) -> str:
    low = reason.lower()
    for cat, keys in _ERROR_CATEGORIES.items():
        if any(k in low for k in keys):
            return cat
    return "otros"


def fpy_stats(dept: Department, month: str | None = None) -> dict:
    """First Pass Yield del mes + % reparadas + categorías de error frecuentes.
    Fuente: eventos `content.copy_review` (uno por intento del ciclo F2.0)."""
    from datetime import datetime, timezone

    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    events = [
        e
        for e in dept.events.fetch(types=["content.copy_review"], limit=10_000)
        if e.occurred_at.startswith(month)
    ]
    por_pieza: dict[str, list] = {}
    for e in events:
        por_pieza.setdefault(e.payload["package_id"], []).append(e.payload)

    total = len(por_pieza)
    first_pass = 0
    approved_after_repair = 0
    rejected = 0
    categorias: dict[str, int] = {}
    for intentos in por_pieza.values():
        intentos.sort(key=lambda p: p["attempt"])
        if intentos[0]["approved"]:
            first_pass += 1
        elif any(p["approved"] for p in intentos):
            approved_after_repair += 1
        else:
            rejected += 1
        for p in intentos:
            if not p["approved"]:
                for r in p.get("reasons", []):
                    cat = _categorize(r)
                    categorias[cat] = categorias.get(cat, 0) + 1

    return {
        "month": month,
        "pieces": total,
        "fpy": round(first_pass / total, 3) if total else None,
        "first_pass": first_pass,
        "approved_after_repair": approved_after_repair,
        "rejected_final": rejected,
        "repair_success_rate": (
            round(approved_after_repair / (total - first_pass), 3)
            if total - first_pass > 0
            else None
        ),
        "error_categories": dict(sorted(categorias.items(), key=lambda kv: -kv[1])),
        "target_fpy": 0.80,
    }
