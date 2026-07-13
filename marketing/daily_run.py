"""Corrida diaria del departamento — M1 (regla #26 del board).

Los 6 requisitos, y dónde se cumplen:
1. Reinicia sin perder información → ledger del día (`mkt_daily_runs`) + cola
   persistente: relanzar el mismo día RESUME (no re-planifica, no duplica).
2. Logs claros → `logging` ("marketing.daily") + wrapper .bat a logs/ del repo.
3. Métricas → telemetría stage_ms + meter existentes + evento `ops.daily_run`.
4. Se recupera de fallos → reintentos de etapa de la cola + resume del día.
5. Ejecutable programado → `python -m marketing.daily_run run` (wrapper
   `run_marketing_daily.bat` para Task Scheduler).
6. Supervisable sin abrir código → `python -m marketing.daily_run status`.

KPI HOR (Hands-Off Rate): % de corridas completadas sin rescate humano. Las
aprobaciones L0 son gobernanza (no cuentan); los rescates sí — se declaran
con `python -m marketing.daily_run intervencion "<motivo>"` (la honestidad
del KPI depende de declararlos; el evento queda en el journal).
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from marketing.experiments import ExperimentRegistry
from marketing.pipeline import PipelineServices, run_pending, submit
from marketing.planner import plan_day
from marketing.playbook import Playbook
from marketing.queue import ContentQueue
from marketing.telemetry import fpy_stats
from org.kernel.department import Department

logger = logging.getLogger("marketing.daily")

_EC_TZ = timezone(timedelta(hours=-5))

_DDL = """
CREATE TABLE IF NOT EXISTS mkt_daily_runs (
    day          TEXT PRIMARY KEY,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    plan_text    TEXT NOT NULL DEFAULT '',
    package_ids  TEXT NOT NULL DEFAULT '[]',
    result_json  TEXT NOT NULL DEFAULT '{}'
)
"""


def today_ec() -> str:
    return datetime.now(_EC_TZ).date().isoformat()


@dataclass
class DailyContext:
    """Todo lo que la corrida necesita, resuelto por el wrapper de arranque."""

    queue: ContentQueue
    playbook: Playbook
    registry: ExperimentRegistry
    services: PipelineServices
    pillars: list
    objective_by_pillar: dict
    tenant_id: str
    n_briefs: int = 2


class DailyRunner:
    def __init__(self, dept: Department, ctx: DailyContext):
        self._dept = dept
        self._ctx = ctx
        self._store = dept.storage
        self._store.execute(_DDL)

    def _row(self, day: str):
        rows = self._store.query("SELECT * FROM mkt_daily_runs WHERE day = ?", (day,))
        return dict(rows[0]) if rows else None

    def run(self, day: str | None = None) -> dict:
        """Idempotente por día; resumible tras crash en cualquier punto."""
        day = day or today_ec()
        row = self._row(day)
        if row and row["finished_at"]:
            logger.info("daily %s ya terminó — no-op idempotente", day)
            return json.loads(row["result_json"])

        ctx = self._ctx
        if row is None:
            # FASE PLAN: se persiste ANTES de producir (crash-safe)
            logger.info("daily %s: planificando %d piezas", day, ctx.n_briefs)
            plan = plan_day(
                self._dept,
                tenant_id=ctx.tenant_id,
                pillars=ctx.pillars,
                rules=ctx.playbook.active_rules(),
                latest_verdicts=ctx.registry.latest_verdicts(),
                profile=ctx.services.profile,
                n_briefs=ctx.n_briefs,
                objective_by_pillar=ctx.objective_by_pillar,
                rng=random.Random(int(day.replace("-", ""))),  # determinista por día
            )
            package_ids = []
            for pb in plan.briefs:
                result = submit(self._dept, ctx.queue, pb.brief, ctx.services)
                package_ids.append(result.package.package_id)
                logger.info(
                    "daily %s: %s → %s (%d intentos)",
                    day, result.package.package_id, result.package.status, len(result.attempts),
                )
            self._store.execute(
                "INSERT INTO mkt_daily_runs (day, started_at, plan_text, package_ids)"
                " VALUES (?, ?, ?, ?)",
                (
                    day,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    plan.explain(),
                    json.dumps(package_ids),
                ),
            )
        else:
            logger.info("daily %s: RESUME (plan ya existía; produciendo pendientes)", day)

        # FASE PRODUCCIÓN: la cola reanuda lo que falte (idempotente por diseño)
        stats = run_pending(self._dept, ctx.queue, ctx.services)

        row = self._row(day)
        ids = json.loads(row["package_ids"])
        estados = {}
        pendientes = 0
        for pid in ids:
            status = ctx.queue.get(pid).status
            estados[status] = estados.get(status, 0) + 1
            if status not in ("qa_approved", "qa_rejected"):
                pendientes += 1

        result = {
            "day": day,
            "plan_size": len(ids),
            "estados": estados,
            "queue_stats": stats,
            "fpy_mes": fpy_stats(self._dept).get("fpy"),
            "gasto_mes_usd": round(self._dept.meter.month_usd(), 4),
            "completa": pendientes == 0,
        }
        if pendientes == 0:
            self._store.execute(
                "UPDATE mkt_daily_runs SET finished_at = ?, result_json = ? WHERE day = ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 json.dumps(result), day),
            )
            logger.info("daily %s COMPLETA: %s", day, estados)
        else:
            logger.warning(
                "daily %s INCOMPLETA: %d piezas pendientes (reintenta el próximo run)",
                day, pendientes,
            )
        self._dept.emit(
            "ops.daily_run",
            {**result, "resumed": bool(row and not row.get("finished_at") and pendientes == 0)},
            correlation_id=f"daily-{day}",
        )
        return result

    def status(self, day: str | None = None) -> dict:
        """Supervisión sin abrir el código (requisito 6)."""
        day = day or today_ec()
        row = self._row(day)
        if row is None:
            return {"day": day, "estado": "sin corrida", "queue": self._ctx.queue.stats()}
        ids = json.loads(row["package_ids"])
        piezas = []
        for pid in ids:
            attempts, last_error = self._ctx.queue.attempts(pid)
            piezas.append(
                {"package_id": pid, "status": self._ctx.queue.get(pid).status,
                 "errores_de_etapa": attempts, "ultimo_error": last_error}
            )
        return {
            "day": day,
            "estado": "completa" if row["finished_at"] else "en curso / interrumpida",
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "piezas": piezas,
            "queue": self._ctx.queue.stats(),
            "hor_mes": hor_stats(self._dept),
        }


def log_manual_intervention(dept: Department, reason: str) -> None:
    """Declarar un rescate manual (baja el HOR — la honestidad del KPI)."""
    dept.emit("ops.manual_intervention", {"reason": reason[:300]})
    dept.decide(f"intervención manual declarada: {reason[:200]}", correlation_id="ops")


def _cli(argv: list[str]) -> int:
    """CLI de operación (regla #26, requisitos 5 y 6):
    run | status [YYYY-MM-DD] | intervencion "<motivo>" | hor"""
    import sys as _sys

    if hasattr(_sys.stdout, "reconfigure"):
        _sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    from marketing.bootstrap import build_production

    dept, ctx = build_production("biodegradables")
    runner = DailyRunner(dept, ctx)
    cmd = argv[0] if argv else "status"
    if cmd == "run":
        result = runner.run(argv[1] if len(argv) > 1 else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["completa"] else 1
    if cmd == "status":
        print(json.dumps(runner.status(argv[1] if len(argv) > 1 else None),
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "intervencion":
        log_manual_intervention(dept, argv[1] if len(argv) > 1 else "sin motivo declarado")
        print("intervención registrada (afecta el HOR — gracias por la honestidad)")
        return 0
    if cmd == "hor":
        print(json.dumps(hor_stats(dept), ensure_ascii=False, indent=2))
        return 0
    print(__doc__)
    return 2


def hor_stats(dept: Department, month: str | None = None) -> dict:
    """Hands-Off Rate: corridas completas sin rescate / corridas totales."""
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    runs = [
        e for e in dept.events.fetch(types=["ops.daily_run"], limit=10_000)
        if e.occurred_at.startswith(month) and e.payload.get("completa")
    ]
    days = {e.payload["day"] for e in runs}
    interventions = [
        e for e in dept.events.fetch(types=["ops.manual_intervention"], limit=10_000)
        if e.occurred_at.startswith(month)
    ]
    total = len(days)
    return {
        "month": month,
        "runs_completos": total,
        "intervenciones_manuales": len(interventions),
        "hor": round(max(total - len(interventions), 0) / total, 3) if total else None,
    }


if __name__ == "__main__":  # pragma: no cover
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
