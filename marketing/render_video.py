"""Render del video final — F1.5 (ROADMAP.md).

Orquesta el proyecto Remotion (`marketing/render/`) desde Python: prepara los
assets en `public/`, arma los props (escenas con duración derivada de los word
boundaries del TTS — cero probing de audio), invoca `npx remotion render` y
aplica el QA técnico. Devuelve el package con el MP4 final y la portada.

Diseño:
- `runner` inyectable → tests sin Node/Chrome; el runner real es subprocess.
- Duración de escena = último end_ms de sus palabras + PAD_MS (mín MIN_SCENE_MS).
- QA técnico: exit 0 + archivo + tamaño mínimo. La resolución/fps/duración son
  deterministas (van en los props). Loudness: deuda registrada en ROADMAP.
- Licencia Remotion: gratuita para equipos ≤3 (remotion.pro/license) — nuestro caso.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

from marketing.models import AssetRef, ContentPackage
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department

logger = logging.getLogger("marketing.render")

VERSION = "0.1"
RENDER_DIR = Path(__file__).parent / "render"
ENTRY = "src/index.ts"
COMPOSITION = "VerticalVideo"
PAD_MS = 450.0
MIN_SCENE_MS = 1500.0
MIN_MP4_BYTES = 100_000
COVER_FRAME = 20
DEFAULT_BRAND_COLOR = "#1B7A43"


class RenderError(RuntimeError):
    pass


def _npx() -> str:
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npx:
        raise RenderError("npx no está en el PATH — instalar Node.js (ver ROADMAP F1.5)")
    return npx


def _real_runner(args: list[str]) -> None:
    result = subprocess.run(
        [_npx(), "remotion", *args],
        cwd=RENDER_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",  # la consola Windows es cp1252; Remotion emite UTF-8
        timeout=1800,
    )
    if result.returncode != 0:
        raise RenderError(f"remotion {args[0]} falló (exit {result.returncode}): {result.stderr[-2000:]}")


def _scene_assets(package: ContentPackage, kind: str) -> dict[int, AssetRef]:
    return {
        a.scene_index: a
        for a in package.assets
        if a.kind == kind and a.scene_index is not None
    }


def build_props(package: ContentPackage, brand_color: str) -> dict:
    """Props del template Remotion. Público para poder testearlo aislado."""
    audios = _scene_assets(package, "audio")
    videos = _scene_assets(package, "video")
    scenes = []
    for i in range(len(package.scenes)):
        if i not in audios:
            raise RenderError(f"escena {i} sin audio TTS — correr synthesize_package antes")
        if i not in videos:
            raise RenderError(f"escena {i} sin b-roll — correr fetch_broll_for_package antes")
        words = [t for t in package.word_timings if t.scene_index == i]
        if not words:
            raise RenderError(f"escena {i} sin word timings")
        duration = max(max(w.end_ms for w in words) + PAD_MS, MIN_SCENE_MS)
        scenes.append(
            {
                "audio": f"{package.package_id}/scene{i:02d}.mp3",
                "video": f"{package.package_id}/scene{i:02d}.mp4",
                "duration_ms": duration,
                "words": [
                    {"word": w.word, "start_ms": w.start_ms, "end_ms": w.end_ms} for w in words
                ],
            }
        )
    return {
        "title": package.title,
        "hook": package.hook,
        "brand_color": brand_color,
        "scenes": scenes,
    }


def _stage_assets(package: ContentPackage) -> None:
    """Copia los assets del package a render/public/<pkg_id>/ (staticFile)."""
    dest = RENDER_DIR / "public" / package.package_id
    dest.mkdir(parents=True, exist_ok=True)
    for kind, ext in (("audio", "mp3"), ("video", "mp4")):
        for i, asset in _scene_assets(package, kind).items():
            src = Path(asset.path)
            if not src.exists():
                raise RenderError(f"asset no existe en disco: {src}")
            shutil.copyfile(src, dest / f"scene{i:02d}.{ext}")


def render_package(
    dept: Department,
    package: ContentPackage,
    out_dir: str | Path,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    runner=None,
) -> ContentPackage:
    """Renderiza el MP4 final + portada. Devuelve un package NUEVO en estado
    'produced' (pendiente del gate de calidad F1.7)."""
    if any(a.kind == "video" and a.scene_index is None for a in package.assets):
        raise RenderError(f"package {package.package_id} ya tiene render final")

    run = runner or _real_runner
    props = build_props(package, brand_color)  # valida completitud ANTES de gastar render
    _stage_assets(package)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    props_path = RENDER_DIR / "public" / package.package_id / "props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    mp4 = out / f"{package.package_id}.mp4"
    cover = out / f"{package.package_id}-cover.jpg"
    total_ms = sum(s["duration_ms"] for s in props["scenes"])
    with tel_stage(dept, package.package_id, "render") as info:
        # --timeout: el delayRender default (28s) se queda corto decodificando
        # b-roll pesado en CPU modesta (falló la pieza 4 del lote F1.8)
        run(["render", ENTRY, COMPOSITION, str(mp4), f"--props={props_path}", "--timeout=120000"])
        run(["still", ENTRY, COMPOSITION, str(cover), f"--props={props_path}", f"--frame={COVER_FRAME}"])

        # QA técnico (F1.5): el render existe y pesa lo razonable; props deterministas
        if not mp4.exists() or mp4.stat().st_size < MIN_MP4_BYTES:
            raise RenderError(f"QA: {mp4} no existe o pesa menos de {MIN_MP4_BYTES} bytes")
        if not cover.exists() or cover.stat().st_size == 0:
            raise RenderError(f"QA: portada {cover} inválida")
        info.update(duration_ms=total_ms, mp4_bytes=mp4.stat().st_size)
    dept.meter.record(
        "render", qty=1, usd=0.0, meta={"engine": f"remotion@{VERSION}", "duration_ms": total_ms}
    )
    dept.decide(
        f"video renderizado para {package.package_id} ({len(props['scenes'])} escenas, "
        f"{total_ms / 1000:.1f}s, 1080x1920@30)",
        context_refs=[f"package:{package.package_id}", f"mp4:{mp4}"],
        correlation_id=package.package_id,
    )
    new_assets = [
        AssetRef(kind="video", path=str(mp4), source=f"render:remotion@{VERSION}"),
        AssetRef(kind="cover", path=str(cover), source=f"render:remotion@{VERSION}"),
    ]
    return package.model_copy(
        update={"assets": list(package.assets) + new_assets, "status": "produced"}
    )
