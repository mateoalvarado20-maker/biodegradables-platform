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
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department
from org.kernel.llm import _tok, record_llm_call

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
    # Directriz #13 del board: estándar 20-30 s; formatos más largos solo con
    # evidencia experimental (el Analista propondrá briefs fuera de rango).
    duration_target_s: int = Field(default=25, ge=20, le=30)
    hypothesis: Hypothesis


def _default_llm_call(system: str, messages: list[dict]) -> tuple[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # Prompt caching (directriz #12): el system (contexto de marca + reglas)
        # es idéntico entre generaciones del mismo tenant — cache write 1.25x
        # una vez, reads a 0.1x el resto del batch. llm_usage ya tarifica
        # cache_creation/cache_read por separado.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return resp.content[0].text, resp.usage


def _system_prompt(format: Format, profile: PlatformProfile, brand_context: str) -> str:
    """ESTABLE por (tenant, formato, red): es la clave del prompt caching
    (directriz #12). Todo lo que varía por pieza va en el mensaje de usuario."""
    if format == "carousel":
        estructura = (
            'slides (lista de 5 a 7 objetos {"title": str ≤60 chars, "body": str ≤200 chars} — '
            "el primero es la portada-gancho, el último cierra con el CTA)"
        )
        duracion = "- Cada slide se lee en 2-3 segundos: textos cortos y concretos."
    else:
        estructura = (
            'scenes (lista de objetos {"voice_text": str, "broll_keywords": lista de str '
            'en inglés para stock, "on_screen_text": str o null})'
        )
        duracion = (
            "- DURACIÓN ESTRICTA: el guion hablado completo debe durar entre 20 y 30 "
            "segundos — entre 55 y 80 palabras EN TOTAL sumando todas las escenas. "
            "Máximo 5 escenas. (El mensaje del usuario puede afinar el objetivo.)"
        )
    return f"""Eres el guionista senior del departamento de Marketing.
Escribes contenido {format} vertical para {profile.platform} en español latino.

CONTEXTO DE MARCA (fuente de verdad — no inventes datos fuera de esto):
{brand_context}

RESTRICCIONES:
{duracion}
- Caption: máximo {profile.caption_max_chars} caracteres.
- Hashtags: máximo {profile.hashtags_max}, sin '#' y sin espacios.

REGLAS:
- Hook en los primeros 2 segundos, del tipo que pida el usuario.
- CTA del tipo que pida el usuario.
- Nada de claims no sustentados por el contexto de marca.
- Responde SOLO con un objeto JSON, sin markdown ni texto extra, con las claves:
  title (str, ≤90 chars), hook (str), {estructura},
  caption (str), hashtags (lista de str sin '#'), cta (str)."""


def _user_message(brief: ScriptBrief) -> str:
    partes = [
        f"Pilar: {brief.pillar_id}.",
        f"Tema sugerido: {brief.topic_hint}." if brief.topic_hint else "",
        f"Tipo de gancho: {brief.hook_type}.",
        f"Tipo de CTA: {brief.cta_type}.",
        (
            f"Duración objetivo: ~{brief.duration_target_s}s hablados."
            if brief.format == "video"
            else ""
        ),
        f"Hipótesis a validar: {brief.hypothesis.question} (métrica: {brief.hypothesis.metric}).",
        "Escribe el contenido.",
    ]
    return " ".join(p for p in partes if p)


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
    system = _system_prompt(brief.format, profile, brand_context)
    messages: list[dict] = [{"role": "user", "content": _user_message(brief)}]

    last_error: Exception | None = None
    with tel_stage(dept, package_id, "guion") as info:
        info.update(attempts=0, tokens=0)
        for _attempt in range(1 + MAX_RETRIES):
            text, usage = call(system, messages)
            record_llm_call(dept, "guionista", MODEL, usage)
            info["attempts"] += 1
            info["tokens"] += _tok(usage, "input_tokens") + _tok(usage, "output_tokens")
            info["cache_read_tokens"] = info.get("cache_read_tokens", 0) + _tok(
                usage, "cache_read_input_tokens"
            )
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
                    scenes=data.get("scenes", []) if brief.format == "video" else [],
                    slides=data.get("slides", []) if brief.format == "carousel" else [],
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
