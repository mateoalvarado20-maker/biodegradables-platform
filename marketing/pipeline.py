"""Runner del pipeline sobre la cola persistente — F2.0d (ROADMAP.md).

`submit()` corre el ciclo de reparación (F2.0b) y encola el resultado;
`advance()` ejecuta LA SIGUIENTE etapa pendiente de un package según su estado
persistido; `run_pending()` avanza todo lo procesable hasta estado terminal o
error. Matar el proceso en cualquier punto y relanzar `run_pending()` reanuda
desde los estados guardados — sin duplicar (guards de etapa) ni perder (todo
avance queda en la cola ANTES de pasar a la siguiente etapa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from marketing.broll import fetch_broll_for_package
from marketing.carousel import render_carousel
from marketing.gate import review_package
from marketing.guionista import ScriptBrief
from marketing.models import ContentPackage, PlatformProfile
from marketing.queue import ContentQueue
from marketing.render_video import render_package
from marketing.repair import RepairResult, generate_with_repair
from marketing.tts import synthesize_package
from org.kernel.department import Department

logger = logging.getLogger("marketing.pipeline")

MAX_STAGE_ATTEMPTS = 3  # tras 3 errores de etapa, el package queda en revisión manual


@dataclass
class PipelineServices:
    """Todo lo que las etapas necesitan (inyectable en tests)."""

    profile: PlatformProfile
    brand_context: str
    voice: str
    out_dir: Path
    brand_color: str = "#1B7A43"
    brand_name: str = ""
    # inyectables (None = servicios reales)
    gen_llm_call: object = None
    review_llm_call: object = None
    synth_fn: object = None
    fetch_fn: object = None
    runner: object = None
    extra: dict = field(default_factory=dict)


def submit(
    dept: Department, queue: ContentQueue, brief: ScriptBrief, services: PipelineServices
) -> RepairResult:
    """Genera con ciclo de reparación y encola el resultado (aprobado o
    rechazado definitivo — ambos se persisten: el rechazo también es dato)."""
    result = generate_with_repair(
        dept,
        brief,
        services.profile,
        services.brand_context,
        gen_llm_call=services.gen_llm_call,
        review_llm_call=services.review_llm_call,
    )
    queue.enqueue(result.package, brief.model_dump())
    return result


def advance(
    dept: Department, queue: ContentQueue, package_id: str, services: PipelineServices
) -> ContentPackage:
    """Ejecuta la siguiente etapa de un package según su estado persistido y
    guarda el resultado. Una etapa por llamada (crash-safe por construcción)."""
    package = queue.get(package_id)

    if package.status == "copy_approved":
        if package.labels.format == "video":
            package = synthesize_package(
                dept, package, services.voice, services.out_dir / "voz",
                synth_fn=services.synth_fn,
            )
            package = fetch_broll_for_package(
                dept, package, services.out_dir / "broll", fetch_fn=services.fetch_fn
            )
            package = render_package(
                dept, package, services.out_dir,
                brand_color=services.brand_color, runner=services.runner,
            )
        else:
            package = render_carousel(
                dept, package, services.out_dir,
                brand_color=services.brand_color,
                brand_name=services.brand_name,
                runner=services.runner,
            )
        queue.save(package)  # status: produced
        return package

    if package.status == "produced":
        package = review_package(
            dept, package, services.profile, services.brand_context,
            llm_call=services.review_llm_call,
        )
        queue.save(package)  # status: qa_approved | qa_rejected
        return package

    return package  # terminal o fuera del alcance del runner (scheduled/published)


def run_pending(
    dept: Department, queue: ContentQueue, services: PipelineServices
) -> dict[str, int]:
    """Avanza todo lo pendiente hasta estado terminal. Reejecutable tras un
    crash: reanuda desde los estados persistidos. Los errores de etapa se
    registran y el package queda en cola para el siguiente run (hasta
    MAX_STAGE_ATTEMPTS)."""
    for package_id in queue.pending():
        while True:
            package = queue.get(package_id)
            if package.status in ("qa_approved", "qa_rejected", "scheduled", "published"):
                break
            attempts, _ = queue.attempts(package_id)
            if attempts >= MAX_STAGE_ATTEMPTS:
                logger.warning(
                    "%s: %d errores de etapa — queda para revisión manual",
                    package_id, attempts,
                )
                break
            try:
                advance(dept, queue, package_id, services)
            except Exception as exc:
                queue.mark_error(package_id, f"{type(exc).__name__}: {exc}")
                dept.decide(
                    f"error de etapa en {package_id} (intento {attempts + 1}/"
                    f"{MAX_STAGE_ATTEMPTS}): {str(exc)[:200]}",
                    correlation_id=package_id,
                )
                break
    return queue.stats()
