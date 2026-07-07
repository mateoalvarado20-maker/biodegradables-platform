"""TTS con word boundaries — F1.3 (ROADMAP.md).

Sintetiza la voz de cada escena del ContentPackage con Azure Speech neural y
persiste en el package los AssetRef de audio + los WordTiming por palabra
(la materia prima de los subtítulos karaoke: cero transcripción, sync exacto).

Diseño:
- Backend inyectable (`synth_fn`) → tests sin red y proveedor sustituible
  (invariante VER-OS #8). El backend real usa el SDK oficial
  `azure-cognitiveservices-speech` (única forma de obtener word boundaries;
  el REST simple no los expone — justificación de la dependencia en
  requirements-marketing.txt).
- La voz es DATO del tenant (`tenants/<slug>/marketing.yaml: tts_voice`),
  nunca constante del módulo (directriz #11).
- Metering: unidad `tts_chars` por síntesis (usd=0.0 en tier F0; si se migra
  a S0, cambiar PRICE_USD_PER_MCHAR y el meter refleja el costo real).
- Medible (directriz #10): AssetRef.source = "tts:azure:<voz>@<versión>".
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

from marketing.models import AssetRef, ContentPackage, WordTiming
from org.kernel.department import Department

logger = logging.getLogger("marketing.tts")

VERSION = "0.1"
# Tier F0: 500k chars neural/mes sin costo. En S0 sería ~$16/M chars.
PRICE_USD_PER_MCHAR = 0.0

# SynthFn: (texto, voz, ruta_salida) -> lista de (palabra, start_ms, end_ms)
SynthFn = Callable[[str, str, Path], list[tuple[str, float, float]]]


class TtsError(RuntimeError):
    pass


def _azure_synth_fn(text: str, voice: str, out_path: Path) -> list[tuple[str, float, float]]:
    """Backend real. Import perezoso: los tests con fake no necesitan el SDK."""
    import os

    import azure.cognitiveservices.speech as speechsdk

    key = os.environ.get("AZURE_SPEECH_KEY")
    region = os.environ.get("AZURE_SPEECH_REGION")
    if not key or not region:
        raise TtsError("faltan AZURE_SPEECH_KEY / AZURE_SPEECH_REGION en el entorno")

    lang = "-".join(voice.split("-")[:2])  # es-EC-AndreaNeural -> es-EC
    ssml = (
        f'<speak version="1.0" xml:lang="{lang}">'
        f'<voice name="{voice}">{escape(text)}</voice></speak>'
    )

    config = speechsdk.SpeechConfig(subscription=key, region=region)
    config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3
    )
    config.set_property(
        speechsdk.PropertyId.SpeechServiceResponse_RequestWordBoundary, "true"
    )
    audio_cfg = speechsdk.audio.AudioOutputConfig(filename=str(out_path))
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=audio_cfg)

    words: list[tuple[str, float, float]] = []

    def on_boundary(evt):
        if evt.boundary_type != speechsdk.SpeechSynthesisBoundaryType.Word:
            return
        start_ms = evt.audio_offset / 10_000  # ticks de 100 ns → ms
        dur_ms = evt.duration.total_seconds() * 1000
        words.append((evt.text, start_ms, start_ms + max(dur_ms, 1.0)))

    synthesizer.synthesis_word_boundary.connect(on_boundary)
    result = synthesizer.speak_ssml_async(ssml).get()

    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        detail = ""
        if result.reason == speechsdk.ResultReason.Canceled:
            detail = f": {result.cancellation_details.reason} {result.cancellation_details.error_details}"
        raise TtsError(f"síntesis falló ({result.reason}){detail}")
    if not words:
        raise TtsError("síntesis sin word boundaries — revisar RequestWordBoundary")
    return words


def synthesize_package(
    dept: Department,
    package: ContentPackage,
    voice: str,
    out_dir: str | Path,
    *,
    synth_fn: SynthFn | None = None,
) -> ContentPackage:
    """Sintetiza la voz de cada escena. Devuelve un package NUEVO con los
    AssetRef de audio y los word_timings persistidos (el original no se muta)."""
    if not package.scenes:
        raise TtsError(f"package {package.package_id} sin escenas — nada que sintetizar")
    if package.word_timings:
        raise TtsError(f"package {package.package_id} ya tiene voz sintetizada")

    synth = synth_fn or _azure_synth_fn
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    assets: list[AssetRef] = []
    timings: list[WordTiming] = []
    total_chars = 0
    for i, scene in enumerate(package.scenes):
        path = out / f"{package.package_id}-scene{i:02d}.mp3"
        words = synth(scene.voice_text, voice, path)
        if not words:
            raise TtsError(f"escena {i}: el backend no devolvió word boundaries")
        total_chars += len(scene.voice_text)
        assets.append(
            AssetRef(
                kind="audio",
                path=str(path),
                source=f"tts:azure:{voice}@{VERSION}",
                license_note="voz sintética propia — sin restricciones",
            )
        )
        timings.extend(
            WordTiming(scene_index=i, word=w, start_ms=s, end_ms=e) for w, s, e in words
        )

    usd = round(total_chars * PRICE_USD_PER_MCHAR / 1_000_000, 6)
    dept.meter.record(
        "tts_chars", qty=total_chars, usd=usd, meta={"voice": voice, "tier": "f0"}
    )
    dept.decide(
        f"voz sintetizada para {package.package_id} ({len(package.scenes)} escenas, "
        f"{total_chars} chars, voz {voice})",
        context_refs=[f"package:{package.package_id}"],
        correlation_id=package.package_id,
    )
    return package.model_copy(
        update={
            "assets": list(package.assets) + assets,
            "word_timings": timings,
            "status": "draft",
        }
    )
