"""Render de carruseles — F1.6 (ROADMAP.md).

Un slide = un PNG 1080×1920 renderizado con el MISMO stack Remotion del video
(`remotion still --frame=i` sobre la composición CarouselSlide, donde el frame
i muestra el slide i). Decisión de diseño registrada: el plan original decía
"HTML → screenshot Playwright", pero reutilizar Remotion da estética idéntica
al video, cero dependencias nuevas (regla #3) y un solo stack de plantillas.

`runner` inyectable igual que render_video (tests sin Node).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from marketing.models import AssetRef, ContentPackage
from marketing.render_video import ENTRY, RENDER_DIR, RenderError, _real_runner
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department

logger = logging.getLogger("marketing.carousel")

VERSION = "0.1"
COMPOSITION = "CarouselSlide"
MIN_PNG_BYTES = 5_000
DEFAULT_BRAND_COLOR = "#1B7A43"


def render_carousel(
    dept: Department,
    package: ContentPackage,
    out_dir: str | Path,
    *,
    brand_color: str = DEFAULT_BRAND_COLOR,
    brand_name: str = "",
    runner=None,
) -> ContentPackage:
    """Renderiza un PNG por slide. Devuelve un package NUEVO en 'produced'."""
    if package.labels.format != "carousel":
        raise RenderError(f"package {package.package_id} no es carrusel")
    if not package.slides:
        raise RenderError(f"package {package.package_id} sin slides")
    if any(a.kind == "image" for a in package.assets):
        raise RenderError(f"package {package.package_id} ya tiene slides renderizados")

    run = runner or _real_runner
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    staging = RENDER_DIR / "public" / package.package_id
    staging.mkdir(parents=True, exist_ok=True)
    props = {
        "brand_color": brand_color,
        "brand_name": brand_name or package.tenant_id,
        "cta": package.cta,
        "slides": [{"title": s.title, "body": s.body} for s in package.slides],
    }
    props_path = staging / "carousel-props.json"
    props_path.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    assets: list[AssetRef] = []
    with tel_stage(dept, package.package_id, "carousel") as info:
        for i in range(len(package.slides)):
            png = out / f"{package.package_id}-slide{i:02d}.png"
            run(["still", ENTRY, COMPOSITION, str(png), f"--props={props_path}", f"--frame={i}"])
            if not png.exists() or png.stat().st_size < MIN_PNG_BYTES:
                raise RenderError(f"QA: slide {i} inválido ({png})")
            assets.append(
                AssetRef(
                    kind="image",
                    path=str(png),
                    source=f"render:remotion-carousel@{VERSION}",
                    scene_index=i,
                )
            )
        info.update(slides=len(assets))

    dept.meter.record("carousel_render", qty=1, usd=0.0, meta={"slides": len(assets)})
    dept.decide(
        f"carrusel renderizado para {package.package_id} ({len(assets)} slides)",
        context_refs=[f"package:{package.package_id}"],
        correlation_id=package.package_id,
    )
    return package.model_copy(
        update={"assets": list(package.assets) + assets, "status": "produced"}
    )
