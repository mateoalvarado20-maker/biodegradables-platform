"""Ciclo de reparación — F2.0b (ROADMAP.md), flujo aprobado por el board:

    Generador → Gate(copy) → Feedback estructurado → Reparación → Gate(copy)
    → (máx MAX_REPAIRS reparaciones) → copy_approved o qa_rejected definitivo.

Corre sobre el BORRADOR (antes de TTS/b-roll/render): reparar copy cuesta
segundos y centavos; reparar después del render costaría ~9 min de render por
intento. El gate final post-producción (`review_package`) sigue siendo la
última barrera antes de publicar.

Cada intento registra (requisito del board): motivo del rechazo, cambios
realizados, si quedó resuelto, tiempo adicional y costo adicional — en el
journal (auditable) y como evento `content.copy_review` (fuente del KPI FPY).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from marketing.gate import CopyVerdict, review_copy
from marketing.guionista import (
    MODEL,
    LlmCall,
    ScriptBrief,
    _default_llm_call,
    _extract_json,
    _system_prompt,
    generate_package,
)
from marketing.models import ContentPackage, PlatformProfile
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department
from org.kernel.llm import record_llm_call

logger = logging.getLogger("marketing.repair")

VERSION = "0.1"
MAX_REPAIRS = 2  # decisión del board: máximo dos reparaciones


@dataclass
class Attempt:
    attempt: int
    approved: bool
    score: int
    reasons: list[str]
    cambios: list[str]  # qué cambió la reparación que PRODUJO este intento
    elapsed_ms: float
    usd: float


@dataclass
class RepairResult:
    package: ContentPackage
    approved: bool
    first_pass: bool  # aprobada al primer intento (numerador del FPY)
    attempts: list[Attempt] = field(default_factory=list)


def revise_package(
    dept: Department,
    package: ContentPackage,
    reasons: list[str],
    brief: ScriptBrief,
    profile: PlatformProfile,
    brand_context: str,
    *,
    llm_call: LlmCall | None = None,
) -> tuple[ContentPackage, list[str]]:
    """Reparación: mismo system prompt (cache hit), feedback del gate como
    instrucción. Devuelve (package corregido con el MISMO package_id, cambios)."""
    call = llm_call or _default_llm_call
    system = _system_prompt(brief.format, profile, brand_context)
    original = {
        "title": package.title,
        "hook": package.hook,
        "scenes": [s.model_dump() for s in package.scenes],
        "slides": [s.model_dump() for s in package.slides],
        "caption": package.caption_master,
        "hashtags": package.hashtags_master,
        "cta": package.cta,
    }
    user = (
        "El revisor de calidad RECHAZÓ tu pieza. Razones:\n"
        + "\n".join(f"- {r}" for r in reasons)
        + "\n\nTu pieza original:\n"
        + json.dumps(original, ensure_ascii=False)
        + "\n\nCorrige TODOS los problemas señalados manteniendo lo que funcionó. "
        "Responde SOLO con el objeto JSON corregido (mismas claves) más una clave "
        'adicional "cambios_realizados" (lista de strings: qué cambiaste y por qué).'
    )
    messages = [{"role": "user", "content": user}]
    last_error: Exception | None = None
    for _ in range(2):
        text, usage = call(system, messages)
        record_llm_call(dept, "guionista-reparador", MODEL, usage)
        try:
            data = _extract_json(text)
            cambios = [str(c) for c in data.pop("cambios_realizados", [])]
            payload = package.model_dump()
            payload.update(
                title=str(data["title"])[:90],
                hook=data["hook"],
                scenes=data.get("scenes", []) if brief.format == "video" else [],
                slides=data.get("slides", []) if brief.format == "carousel" else [],
                caption_master=data["caption"],
                hashtags_master=list(data.get("hashtags", []))[: profile.hashtags_max],
                cta=data["cta"],
                generated_by=f"{package.generated_by}+repair@{VERSION}",
            )
            return ContentPackage(**payload), cambios  # validación completa
        except Exception as exc:  # JSON/validación: reintenta con el error
            last_error = exc
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {"role": "user", "content": f"Tu corrección no validó: {exc}. Solo el JSON."}
            )
    raise RuntimeError(f"reparador sin JSON válido para {package.package_id}: {last_error}")


def generate_with_repair(
    dept: Department,
    brief: ScriptBrief,
    profile: PlatformProfile,
    brand_context: str,
    *,
    gen_llm_call: LlmCall | None = None,
    review_llm_call: LlmCall | None = None,
    max_repairs: int = MAX_REPAIRS,
) -> RepairResult:
    """El flujo completo del board. Devuelve el package en `copy_approved`
    (listo para producción) o `qa_rejected` (rechazo definitivo), con la
    historia completa de intentos."""
    package = generate_package(dept, brief, profile, brand_context, llm_call=gen_llm_call)
    with tel_stage(dept, package.package_id, "repair_cycle") as info:
        attempts: list[Attempt] = []
        cambios_previos: list[str] = []

        for n in range(1, 2 + max_repairs):
            t0 = time.perf_counter()
            usd_before = dept.meter.month_usd()
            verdict: CopyVerdict = review_copy(
                dept, package, profile, brand_context, llm_call=review_llm_call
            )
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            usd = round(dept.meter.month_usd() - usd_before, 6)
            attempts.append(
                Attempt(n, verdict.approved, verdict.score, verdict.reasons, cambios_previos, elapsed_ms, usd)
            )
            # Registro por intento (requisito del board): motivo, cambios,
            # resuelto, tiempo y costo — journal + evento (fuente del FPY)
            dept.decide(
                f"copy-gate intento {n}/{1 + max_repairs} para {package.package_id}: "
                f"{'aprobado' if verdict.approved else 'rechazado'} (score {verdict.score})",
                context_refs=(
                    [f"razón: {r}" for r in verdict.reasons[:5]]
                    + [f"cambio aplicado: {c}" for c in cambios_previos[:5]]
                    + [f"costo intento: ${usd:.4f}", f"tiempo intento: {elapsed_ms:.0f}ms"]
                ),
                correlation_id=package.package_id,
            )
            dept.emit(
                "content.copy_review",
                {
                    "package_id": package.package_id,
                    "attempt": n,
                    "approved": verdict.approved,
                    "score": verdict.score,
                    "deterministic": verdict.deterministic,
                    "reasons": verdict.reasons[:6],
                    "usd": usd,
                    "elapsed_ms": elapsed_ms,
                },
                correlation_id=package.package_id,
            )
            if verdict.approved:
                info.update(attempts=n, approved=1, first_pass=int(n == 1))
                return RepairResult(
                    package.model_copy(update={"status": "copy_approved"}),
                    approved=True,
                    first_pass=(n == 1),
                    attempts=attempts,
                )
            if n > max_repairs:
                break
            t_rep = time.perf_counter()
            usd_before = dept.meter.month_usd()
            package, cambios_previos = revise_package(
                dept, package, verdict.reasons, brief, profile, brand_context,
                llm_call=gen_llm_call,
            )
            logger.info(
                "reparación %d de %s: %d cambios (%.0f ms, $%.4f)",
                n,
                package.package_id,
                len(cambios_previos),
                (time.perf_counter() - t_rep) * 1000,
                dept.meter.month_usd() - usd_before,
            )

        info.update(attempts=len(attempts), approved=0, first_pass=0)
        dept.decide(
            f"rechazo DEFINITIVO de {package.package_id} tras {len(attempts)} intentos",
            context_refs=[f"última razón: {r}" for r in attempts[-1].reasons[:4]],
            correlation_id=package.package_id,
        )
        return RepairResult(
            package.model_copy(update={"status": "qa_rejected"}),
            approved=False,
            first_pass=False,
            attempts=attempts,
        )
