"""Modelos del departamento de Marketing (ROADMAP.md F1.1).

Principios (PROPUESTA_TIKTOK_MEDIA_MANAGER.md §4):
- `ContentPackage` es agnóstico de plataforma — el core no sabe qué es TikTok.
- `PlatformRendition` es la materialización para una red, validada contra el
  `PlatformProfile` declarativo (marketing/profiles/<red>.yaml).
- Los pilares de contenido son HIPÓTESIS gestionadas por datos (decisión del
  board 2026-07-06): nacen `hypothesis` y el Analista (F3) los promueve/retira
  con evidencia — nunca son reglas fijas.
- Todo package lleva `ExperimentLabels` completos: sin etiquetas no hay
  atribución, y sin atribución no hay aprendizaje (se publica a ciegas).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Format = Literal["video", "carousel", "story"]

PackageStatus = Literal[
    "draft",
    "copy_approved",  # pasó el gate de copy (pre-producción, ciclo F2.0)
    "produced",
    "qa_approved",
    "qa_rejected",
    "scheduled",
    "published",
]

PillarStatus = Literal["hypothesis", "validated", "retired"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pillar(StrictModel):
    """Pilar de contenido. Estado gestionado por el Analista con evidencia."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(min_length=3)
    status: PillarStatus = "hypothesis"
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)  # exp_ids que respaldan status

    @model_validator(mode="after")
    def _validated_exige_evidencia(self):
        if self.status == "validated" and not self.evidence:
            raise ValueError(f"pilar {self.id}: 'validated' exige evidence (exp_ids)")
        return self


class Hypothesis(StrictModel):
    """Hipótesis de negocio de una pieza (directriz #7 del board: cada
    publicación es un experimento controlado, no volumen)."""

    question: str = Field(min_length=10)  # ¿qué intentamos aprender?
    metric: str = Field(min_length=3)  # qué métrica decide (views, shares, leads…)
    success_criteria: str = Field(min_length=3)  # umbral o comparación que define éxito
    decision_if_true: str = Field(min_length=3)  # qué haremos si se cumple
    decision_if_false: str = Field(min_length=3)  # qué haremos si no


class ExperimentLabels(StrictModel):
    """Etiquetado experimental obligatorio (dimensiones aprendibles, F2.5)."""

    pillar: str = Field(min_length=2)
    hook_type: str = Field(min_length=2)
    format: Format
    time_slot: str = Field(pattern=r"^\d{2}:\d{2}-\d{2}:\d{2}$")
    cta_type: str = Field(min_length=2)


class Scene(StrictModel):
    voice_text: str = Field(min_length=1)
    broll_keywords: list[str] = Field(default_factory=list)
    on_screen_text: str | None = None


class Slide(StrictModel):
    """Slide de un carrusel (photo post). Textos cortos: se leen en 2-3 s."""

    title: str = Field(min_length=3, max_length=60)
    body: str = Field(min_length=3, max_length=220)


class WordTiming(StrictModel):
    """Timestamp de una palabra hablada (word boundary del TTS, F1.3).

    Es la materia prima de los subtítulos karaoke SIN transcripción: el
    sintetizador ya sabe exactamente cuándo dice cada palabra. Offsets en ms
    relativos al inicio del audio de SU escena."""

    scene_index: int = Field(ge=0)
    word: str = Field(min_length=1)
    start_ms: float = Field(ge=0)
    end_ms: float = Field(gt=0)

    @model_validator(mode="after")
    def _rango(self):
        if self.end_ms <= self.start_ms:
            raise ValueError(f"word {self.word!r}: end_ms debe ser > start_ms")
        return self


class AssetRef(StrictModel):
    kind: Literal["audio", "video", "image", "cover", "subtitles"]
    path: str = Field(min_length=1)
    source: str = ""  # p.ej. "pexels:12345", "tts:azure:es-EC-AndreaNeural"
    license_note: str = ""
    scene_index: int | None = None  # a qué escena pertenece (None = asset global)
    duration_s: float | None = None  # duración del clip (para Loop en el render)


class ContentPackage(StrictModel):
    package_id: str = Field(min_length=8)
    tenant_id: str = Field(min_length=2)
    labels: ExperimentLabels
    hypothesis: Hypothesis  # sin hipótesis de negocio no hay pieza (directriz #7)
    generated_by: str = ""  # "agente@version:modelo" — medibilidad (directriz #10)
    title: str = Field(min_length=3)
    hook: str = Field(min_length=3)
    scenes: list[Scene] = Field(default_factory=list)
    slides: list[Slide] = Field(default_factory=list)  # solo formato carousel
    caption_master: str = Field(min_length=1)
    hashtags_master: list[str] = Field(default_factory=list)
    cta: str = Field(min_length=2)
    music_ref: str | None = None
    assets: list[AssetRef] = Field(default_factory=list)
    word_timings: list[WordTiming] = Field(default_factory=list)  # las llena el TTS (F1.3)
    status: PackageStatus = "draft"
    created_at: str = Field(min_length=10)

    @field_validator("hashtags_master")
    @classmethod
    def _hashtags_sin_numeral(cls, v: list[str]) -> list[str]:
        for tag in v:
            if tag.startswith("#") or " " in tag:
                raise ValueError(f"hashtag {tag!r}: guardar sin '#' y sin espacios")
        return v

    @model_validator(mode="after")
    def _formato_exige_su_contenido(self):
        if self.labels.format == "video" and not self.scenes:
            raise ValueError("un package de video exige al menos una escena")
        if self.labels.format == "carousel" and len(self.slides) < 3:
            raise ValueError("un package de carrusel exige al menos 3 slides")
        return self


class PlatformRendition(StrictModel):
    package_id: str = Field(min_length=8)
    platform: str = Field(min_length=2)
    format: Format
    caption: str = Field(min_length=1)
    hashtags: list[str] = Field(default_factory=list)
    duration_s: float | None = None
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    media_paths: list[str] = Field(default_factory=list)
    cover_path: str | None = None


class PlatformProfile(StrictModel):
    """Reglas declarativas de una red (marketing/profiles/<red>.yaml).

    Adaptar el sistema a una red nueva = escribir su YAML + su adapter;
    el core y los agentes consumen este modelo, nunca constantes hardcodeadas.
    """

    platform: str = Field(min_length=2)
    formats: list[Format] = Field(min_length=1)
    aspect: str = Field(pattern=r"^\d+:\d+$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    min_video_s: float = Field(gt=0)
    max_video_s: float = Field(gt=0)
    caption_max_chars: int = Field(gt=0)
    hashtags_max: int = Field(ge=0)
    carousel_max_slides: int = Field(gt=0)
    platform_cap_posts_per_day: int = Field(gt=0)  # límite de la RED (no del charter)
    min_gap_minutes: int = Field(ge=0)
    posting_windows: list[str] = Field(min_length=1)  # hipótesis inicial; F3 las ajusta
    notes: str = ""

    @field_validator("posting_windows")
    @classmethod
    def _ventanas_validas(cls, v: list[str]) -> list[str]:
        import re

        for w in v:
            if not re.match(r"^\d{2}:\d{2}-\d{2}:\d{2}$", w):
                raise ValueError(f"ventana inválida: {w!r} (formato HH:MM-HH:MM)")
        return v

    @model_validator(mode="after")
    def _rangos_coherentes(self):
        if self.min_video_s >= self.max_video_s:
            raise ValueError("min_video_s debe ser < max_video_s")
        return self

    def validate_rendition(self, r: PlatformRendition) -> list[str]:
        """Devuelve la lista de violaciones (vacía = publicable en esta red)."""
        problems: list[str] = []
        if r.platform != self.platform:
            problems.append(f"rendition de {r.platform!r} validada contra {self.platform!r}")
        if r.format not in self.formats:
            problems.append(f"formato {r.format!r} no soportado por {self.platform}")
        if len(r.caption) > self.caption_max_chars:
            problems.append(
                f"caption de {len(r.caption)} chars supera el máximo {self.caption_max_chars}"
            )
        if len(r.hashtags) > self.hashtags_max:
            problems.append(f"{len(r.hashtags)} hashtags supera el máximo {self.hashtags_max}")
        if (r.width, r.height) != (self.width, self.height):
            problems.append(
                f"resolución {r.width}x{r.height} ≠ {self.width}x{self.height} del perfil"
            )
        if r.format == "video":
            if r.duration_s is None:
                problems.append("video sin duration_s")
            elif not (self.min_video_s <= r.duration_s <= self.max_video_s):
                problems.append(
                    f"duración {r.duration_s}s fuera de [{self.min_video_s}, {self.max_video_s}]"
                )
        if r.format == "carousel":
            if not r.media_paths:
                problems.append("carrusel sin slides")
            elif len(r.media_paths) > self.carousel_max_slides:
                problems.append(
                    f"{len(r.media_paths)} slides supera el máximo {self.carousel_max_slides}"
                )
        return problems
