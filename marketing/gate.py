"""Gate de calidad — F1.7 (ROADMAP.md).

Dos capas, en este orden (la barata primero — directriz #12):

1. **Checks deterministas ($0, sin LLM):** estado `produced`, assets completos
   según formato, duración hablada dentro del estándar 20–30 s (directriz #13,
   con tolerancia ±2 s), límites de la red (caption/hashtags del profile) y
   **claims prohibidos del charter** (regla dura del board — invariante VER-OS:
   jamás editable por un modelo). Si algo falla, se rechaza sin gastar LLM.
2. **Revisor Claude (rúbrica de marca):** tono, claims sustentados por el
   contexto, calidad del hook, coherencia con el pilar. Devuelve score 0–100 +
   razones; aprueba con score ≥ min_score. System prompt estable por tenant
   (cacheable). Registrado en ambos ledgers como agente "revisor".

El resultado queda en journal (razones auditables) y se emite el evento
`content.qa_passed` / `content.qa_rejected`.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from marketing.models import ContentPackage, PlatformProfile
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department
from org.kernel.llm import record_llm_call

logger = logging.getLogger("marketing.gate")

VERSION = "0.1"
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1000
MIN_SCORE_DEFAULT = 75
DURATION_MIN_MS = 18_000  # estándar 20-30 s con tolerancia de 2 s
DURATION_MAX_MS = 32_000

LlmCall = Callable[[str, list[dict]], tuple[str, Any]]

# Regex de emojis (regla dura de marca — rechazo $0 sin LLM). Cubre los bloques
# Unicode de emojis/símbolos que aparecen en copy de redes.
EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # emojis y símbolos suplementarios (👉 🔗 🌱 …)
    "☀-➿"  # misceláneos + dingbats (✅ ❌ ✨ ☀ …)
    "⬀-⯿"  # flechas/estrellas (⭐ …)
    "️"  # variation selector
    "]"
)

WORDS_PER_SECOND = 2.6  # ritmo medido del TTS es-EC (lote F1.8: 4129 chars ≈ 252 s)


class GateError(RuntimeError):
    pass


def estimate_speech_s(package: ContentPackage) -> float:
    words = sum(len(s.voice_text.split()) for s in package.scenes)
    return words / WORDS_PER_SECOND


def _default_llm_call(system: str, messages: list[dict]) -> tuple[str, Any]:
    import anthropic

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )
    return resp.content[0].text, resp.usage


def _package_texts(package: ContentPackage) -> str:
    partes = [package.title, package.hook, package.caption_master, package.cta]
    partes += [s.voice_text for s in package.scenes]
    partes += [s.on_screen_text or "" for s in package.scenes]
    partes += [f"{s.title} {s.body}" for s in package.slides]
    partes += package.hashtags_master
    return "\n".join(p for p in partes if p)


def copy_checks(
    package: ContentPackage, profile: PlatformProfile, hard_rules: dict
) -> list[str]:
    """Checks deterministas a nivel de COPY (aplicables al borrador, $0).
    Los usa tanto el ciclo de reparación (F2.0) como el gate final."""
    problems: list[str] = []

    texto = _package_texts(package)
    emojis = sorted(set(EMOJI_RE.findall(texto)))
    if emojis:
        problems.append(f"emojis prohibidos por la marca: {' '.join(emojis)}")

    if package.labels.format == "video":
        est = estimate_speech_s(package)
        if not (DURATION_MIN_MS / 1000 - 2 <= est <= DURATION_MAX_MS / 1000 + 2):
            problems.append(
                f"duración estimada {est:.0f}s (por conteo de palabras) fuera del "
                "estándar 20-30s (directriz #13)"
            )

    if len(package.caption_master) > profile.caption_max_chars:
        problems.append(
            f"caption de {len(package.caption_master)} chars supera {profile.caption_max_chars}"
        )
    if len(package.hashtags_master) > profile.hashtags_max:
        problems.append(f"{len(package.hashtags_master)} hashtags supera {profile.hashtags_max}")

    texto_lower = texto.lower()
    for claim in hard_rules.get("claims_prohibidos", []):
        if claim.lower() in texto_lower:
            problems.append(f"claim prohibido por el charter: {claim!r}")
    return problems


def deterministic_checks(
    package: ContentPackage, profile: PlatformProfile, hard_rules: dict
) -> list[str]:
    """Violaciones objetivas del package PRODUCIDO (gate final)."""
    problems: list[str] = []

    if package.status != "produced":
        problems.append(f"estado {package.status!r}: el gate revisa packages 'produced'")
        return problems

    fmt = package.labels.format
    if fmt == "video":
        if not any(a.kind == "video" and a.scene_index is None for a in package.assets):
            problems.append("sin video final renderizado")
        if not any(a.kind == "cover" for a in package.assets):
            problems.append("sin portada")
        if package.word_timings:
            speech_ms = 0.0
            for i in range(len(package.scenes)):
                scene_words = [t for t in package.word_timings if t.scene_index == i]
                if scene_words:
                    speech_ms += max(t.end_ms for t in scene_words)
            if not (DURATION_MIN_MS <= speech_ms <= DURATION_MAX_MS):
                problems.append(
                    f"duración hablada {speech_ms / 1000:.1f}s fuera del estándar 20-30s "
                    "(directriz #13)"
                )
        else:
            problems.append("video sin word_timings — pipeline incompleto")
    elif fmt == "carousel":
        if not any(a.kind == "image" for a in package.assets):
            problems.append("carrusel sin slides renderizados")

    # checks de copy (emojis, límites, claims) — sin la duración estimada:
    # aquí ya existe la duración REAL medida por los word timings
    problems.extend(
        p for p in copy_checks(package, profile, hard_rules) if "estimada" not in p
    )
    return problems


def _rubric_system(brand_context: str) -> str:
    return f"""Eres el revisor de calidad del departamento de Marketing. Evalúas piezas
de contenido ANTES de su publicación, contra el contexto de marca.

CONTEXTO DE MARCA (única fuente de verdad para validar claims):
{brand_context}

RÚBRICA (pondera): claims sustentados por el contexto (40%), hook con fuerza
en 2 segundos (20%), tono de marca cercano y profesional en español latino
(20%), CTA claro y coherencia con el pilar (20%).

Responde SOLO con un objeto JSON, sin markdown:
{{"score": int 0-100, "approved": bool, "reasons": [str, máx 4 razones concretas],
"claim_issues": [str, claims dudosos o no sustentados, vacío si ninguno]}}
Sé exigente: approved=true solo si publicarías la pieza tal cual hoy."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*|\s*```$", "", text, flags=re.IGNORECASE)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("la respuesta no contiene un objeto JSON")
    return json.loads(text[start : end + 1])


def _llm_review(
    dept: Department,
    package: ContentPackage,
    brand_context: str,
    call: LlmCall,
    min_score: int,
) -> tuple[bool, int, list[str]]:
    dept.ensure_capability("llm")
    dept.ensure_budget(0.05)
    user = (
        f"Pilar: {package.labels.pillar}. Formato: {package.labels.format}. "
        f"Hipótesis: {package.hypothesis.question}\n\nPIEZA:\n{_package_texts(package)}"
    )
    data: dict | None = None
    messages = [{"role": "user", "content": user}]
    for _attempt in range(2):
        text, usage = call(_rubric_system(brand_context), messages)
        record_llm_call(dept, "revisor", MODEL, usage)
        try:
            data = _extract_json(text)
            break
        except (ValueError, json.JSONDecodeError) as exc:
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": f"No validó: {exc}. Solo el JSON."})
    if data is None:
        raise GateError(f"revisor sin JSON válido para {package.package_id}")
    score = int(data.get("score", 0))
    approved = bool(data.get("approved", False)) and score >= min_score
    reasons = [str(r) for r in data.get("reasons", [])] + [
        f"claim dudoso: {c}" for c in data.get("claim_issues", [])
    ]
    return approved, score, reasons


class CopyVerdict:
    """Veredicto del gate de copy (borrador, pre-producción — ciclo F2.0)."""

    def __init__(self, approved: bool, score: int, reasons: list[str], deterministic: bool):
        self.approved = approved
        self.score = score
        self.reasons = reasons
        self.deterministic = deterministic


def review_copy(
    dept: Department,
    package: ContentPackage,
    profile: PlatformProfile,
    brand_context: str,
    *,
    llm_call: LlmCall | None = None,
    min_score: int = MIN_SCORE_DEFAULT,
) -> CopyVerdict:
    """Gate sobre el BORRADOR: deterministas de copy primero ($0), luego rúbrica
    LLM. NO cambia el estado del package ni journalea — eso lo hace el ciclo de
    reparación, que registra intento por intento."""
    call = llm_call or _default_llm_call
    with tel_stage(dept, package.package_id, "copy_gate") as info:
        problems = copy_checks(package, profile, dept.charter.hard_rules)
        if problems:
            info.update(llm_used=0, deterministic_reject=1)
            return CopyVerdict(False, 0, problems, deterministic=True)
        approved, score, reasons = _llm_review(dept, package, brand_context, call, min_score)
        info.update(llm_used=1, score=score)
        return CopyVerdict(approved, score, reasons, deterministic=False)


def review_package(
    dept: Department,
    package: ContentPackage,
    profile: PlatformProfile,
    brand_context: str,
    *,
    llm_call: LlmCall | None = None,
    min_score: int = MIN_SCORE_DEFAULT,
) -> ContentPackage:
    """Gate FINAL post-producción. Devuelve el package en `qa_approved` o
    `qa_rejected` (nuevo objeto)."""
    call = llm_call or _default_llm_call
    with tel_stage(dept, package.package_id, "gate") as info:
        problems = deterministic_checks(package, profile, dept.charter.hard_rules)
        if problems:
            info.update(llm_used=0, deterministic_reject=1)
            verdict, score, reasons = "qa_rejected", 0, problems
        else:
            approved, score, reasons = _llm_review(dept, package, brand_context, call, min_score)
            info.update(llm_used=1, score=score)
            verdict = "qa_approved" if approved else "qa_rejected"

    dept.decide(
        f"gate {verdict} para {package.package_id} (score {score})",
        context_refs=[f"package:{package.package_id}"] + [f"razón: {r}" for r in reasons[:6]],
        correlation_id=package.package_id,
    )
    dept.emit(
        "content.qa_passed" if verdict == "qa_approved" else "content.qa_rejected",
        {"package_id": package.package_id, "score": score, "reasons": reasons[:6]},
        correlation_id=package.package_id,
    )
    return package.model_copy(update={"status": verdict})
