"""Guionista — agente de F1.2 (ROADMAP.md).

Recibe un ScriptBrief (pilar + formato + hipótesis de negocio + restricciones
del PlatformProfile) y produce un ContentPackage validado. Diseño:

- Presupuesto ANTES de gastar: `dept.ensure_budget()` (corte duro del charter).
- Cada llamada se registra en ambos ledgers (`org.kernel.llm.record_llm_call`).
- Salida JSON validada por pydantic; ante JSON inválido reintenta con el error
  como feedback (máx MAX_RETRIES), cada reintento también se mide.
- Medible (directriz #10): el package lleva `generated_by = guionista@VER:modelo`
  y la decisión queda en el journal con correlation_id = package_id.
- Agnóstico de cliente (directriz #11): el contexto de marca llega como string
  (lo resuelve `marketing.brand` desde `tenants/<slug>/marketing.yaml`), y las
  restricciones de red llegan del PlatformProfile — nada hardcodeado aquí.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import Field, ValidationError

from marketing.models import (
    ContentPackage,
    ExperimentLabels,
    Format,
    Hypothesis,
    PlatformProfile,
    StrictModel,
)
from org.kernel.department import Department
from org.kernel.llm import record_llm_call

VERSION = "0.1"
MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 2
MAX_TOKENS = 2000
_EC_TZ = timezone(timedelta(hours=-5))

# LlmCall: (system, messages) -> (texto_respuesta, usage). Inyectable en tests
# y sustituible por otro proveedor (invariante VER-OS #8: neutralidad de modelo).
LlmCall = Callable[[str, list[dict]], tuple[str, Any]]


class ScriptBrief(StrictModel):
    tenant_id: str = Field(min_length=2)
    pillar_id: str = Field(min_length=2)
    topic_hint: str = ""  # opcional: tema sugerido por el Planificador
    format: Format = "video"
    hook_type: str = Field(min_length=2)
    cta_type: str = Field(min_length=2)
    time_slot: str = Field(pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")
    duration_target_s: int = Field(default=35, gt=4, le=600)
    hypothesis: Hypothesis


def _default_llm_call(system: str, messages: list[dict]) -> tuple[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages
    )
    return resp.content[0].text, resp.usage


def _system_prompt(brief: ScriptBrief, profile: PlatformProfile, brand_context: str) -> str:
    return f"""Eres el guionista senior del departamento de Marketing.
Escribes guiones de {brief.format} vertical para {profile.platform} en español latino.

CONTEXTO DE MARCA (fuente de verdad — no inventes datos fuera de esto):
{brand_context}

RESTRICCIONES DE LA RED:
- Duración objetivo: ~{brief.duration_target_s}s (máx {profile.max_video_s:.0f}s).
- Caption: máximo {profile.caption_max_chars} caracteres.
- Hashtags: máximo {profile.hashtags_max}, sin '#' y sin espacios.

REGLAS:
- Hook en los primeros 2 segundos (tipo de gancho pedido: {brief.hook_type}).
- CTA del tipo: {brief.cta_type}.
- Nada de claims no sustentados por el contexto de marca.
- Responde SOLO con un objeto JSON, sin markdown ni texto extra, con las claves:
  title (str, ≤90 chars), hook (str), scenes (lista de {{voice_text, broll_keywords, on_screen_text}}),
  caption (str), hashtags (lista de str sin '#'), cta (str)."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(text[start : end + 1])


def generate_package(
    dept: Department,
    brief: ScriptBrief,
    profile: PlatformProfile,
    brand_context: str,
    *,
    llm_call: LlmCall | None = None,
) -> ContentPackage:
    """Genera un ContentPackage validado o lanza tras agotar reintentos."""
    dept.ensure_capability("llm")
    # Techo conservador por generación, verificado ANTES de gastar (el costo real
    # medido con company_context.md completo es ~$0.05; margen para reintentos).
    dept.ensure_budget(0.10)

    call = llm_call or _default_llm_call
    package_id = f"pkg-{uuid.uuid4().hex[:12]}"
    system = _system_prompt(brief, profile, brand_context)
    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                f"Pilar: {brief.pillar_id}. "
                + (f"Tema sugerido: {brief.topic_hint}. " if brief.topic_hint else "")
                + f"Hipótesis a validar: {brief.hypothesis.question} "
                f"(métrica: {brief.hypothesis.metric}). Escribe el guion."
            ),
        }
    ]

    last_error: Exception | None = None
    for _attempt in range(1 + MAX_RETRIES):
        text, usage = call(system, messages)
        record_llm_call(dept, "guionista", MODEL, usage)
        try:
            data = _extract_json(text)
            package = ContentPackage(
                package_id=package_id,
                tenant_id=brief.tenant_id,
                labels=ExperimentLabels(
                    pillar=brief.pillar_id,
                    hook_type=brief.hook_type,
                    format=brief.format,
                    time_slot=brief.time_slot,
                    cta_type=brief.cta_type,
                ),
                hypothesis=brief.hypothesis,
                generated_by=f"guionista@{VERSION}:{MODEL}",
                title=str(data["title"])[:90],
                hook=data["hook"],
                scenes=data.get("scenes", []),
                caption_master=data["caption"],
                hashtags_master=list(data.get("hashtags", []))[: profile.hashtags_max],
                cta=data["cta"],
                created_at=datetime.now(_EC_TZ).isoformat(timespec="seconds"),
            )
        except (ValueError, KeyError, ValidationError) as exc:
            last_error = exc
            messages.append({"role": "assistant", "content": text})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Tu respuesta no validó: {exc}. Responde de nuevo SOLO con el "
                        "objeto JSON corregido, con exactamente las claves pedidas."
                    ),
                }
            )
            continue
        dept.decide(
            f"guion generado para pilar {brief.pillar_id} ({brief.format})",
            context_refs=[
                f"package:{package.package_id}",
                f"hipótesis: {brief.hypothesis.question}",
                f"generated_by: {package.generated_by}",
            ],
            correlation_id=package.package_id,
        )
        return package

    raise RuntimeError(
        f"guionista: {1 + MAX_RETRIES} intentos sin JSON válido; último error: {last_error}"
    )
