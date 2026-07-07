"""B-roll por escena — F1.4 (ROADMAP.md).

Busca y descarga clips verticales de stock (Pexels, licencia libre) usando los
`broll_keywords` que el Guionista dejó en cada escena, y los persiste como
AssetRef (kind=video, source="pexels:<id>", atribución en license_note,
scene_index para el render).

Diseño:
- Backend inyectable (`fetch_fn`) → tests sin red, proveedor sustituible
  (Pixabay/Coverr serían otro fetch_fn, no otro módulo).
- Dedup dentro del package: dos escenas jamás reciben el mismo clip.
- Cache por archivo: un clip ya descargado (pexels-<id>.mp4) no se re-baja.
- Fallback de query: escena sin keywords → pilar del package.
- Metering `broll_clip` (Pexels es $0; la unidad queda medida igual).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Callable

from marketing.models import AssetRef, ContentPackage
from marketing.telemetry import stage as tel_stage
from org.kernel.department import Department

logger = logging.getLogger("marketing.broll")

VERSION = "0.1"
_API = "https://api.pexels.com/videos/search"

# FetchFn: (query, out_dir, exclude_ids) -> (ruta, source_id, atribución, reused)
# reused=True cuando el clip ya estaba en el cache local (no se re-descargó).
FetchFn = Callable[[str, Path, set[str]], tuple[Path, str, str, bool]]


class BrollError(RuntimeError):
    pass


def _pick_file(video: dict) -> dict | None:
    """Mejor variante: vertical y lo más cercana (por arriba) a 1920 de alto."""
    candidates = [
        f
        for f in video.get("video_files", [])
        if f.get("height") and f.get("width") and f["height"] > f["width"]
    ]
    if not candidates:
        return None
    tall = [f for f in candidates if f["height"] >= 1920]
    pool = tall or candidates
    return min(pool, key=lambda f: abs(f["height"] - 1920))


def _pexels_fetch(query: str, out_dir: Path, exclude_ids: set[str]) -> tuple[Path, str, str]:
    import httpx

    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        raise BrollError("falta PEXELS_API_KEY en el entorno")
    resp = httpx.get(
        _API,
        params={"query": query, "orientation": "portrait", "per_page": 6},
        headers={"Authorization": key},
        timeout=30,
    )
    resp.raise_for_status()
    for video in resp.json().get("videos", []):
        vid = str(video["id"])
        if vid in exclude_ids:
            continue
        best = _pick_file(video)
        if not best:
            continue
        path = out_dir / f"pexels-{vid}.mp4"
        reused = path.exists()
        if not reused:
            with httpx.stream("GET", best["link"], timeout=120, follow_redirects=True) as r:
                r.raise_for_status()
                with open(path, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 16):
                        fh.write(chunk)
        author = (video.get("user") or {}).get("name", "desconocido")
        attribution = f"Pexels License · {author} · {video.get('url', '')}"
        return path, vid, attribution, reused
    raise BrollError(f"sin resultados verticales nuevos en Pexels para {query!r}")


def fetch_broll_for_package(
    dept: Department,
    package: ContentPackage,
    out_dir: str | Path,
    *,
    fetch_fn: FetchFn | None = None,
) -> ContentPackage:
    """Un clip vertical por escena. Devuelve un package NUEVO (no muta)."""
    if not package.scenes:
        raise BrollError(f"package {package.package_id} sin escenas — nada que ilustrar")
    if any(a.kind == "video" and a.scene_index is not None for a in package.assets):
        raise BrollError(f"package {package.package_id} ya tiene b-roll")

    fetch = fetch_fn or _pexels_fetch
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    fallback_query = package.labels.pillar.replace("-", " ")
    used_ids: set[str] = set()
    assets: list[AssetRef] = []
    reused_count = 0
    with tel_stage(dept, package.package_id, "broll") as info:
        for i, scene in enumerate(package.scenes):
            query = " ".join(scene.broll_keywords) or fallback_query
            try:
                path, source_id, attribution, reused = fetch(query, out, used_ids)
            except BrollError:
                if query == fallback_query:
                    raise
                logger.warning(
                    "broll: sin resultados para %r; fallback %r", query, fallback_query
                )
                path, source_id, attribution, reused = fetch(fallback_query, out, used_ids)
            used_ids.add(source_id)
            reused_count += int(reused)
            assets.append(
                AssetRef(
                    kind="video",
                    path=str(path),
                    source=f"pexels:{source_id}",
                    license_note=attribution,
                    scene_index=i,
                )
            )
        info.update(clips=len(assets), reused_from_cache=reused_count)

    dept.meter.record("broll_clip", qty=len(assets), usd=0.0, meta={"provider": "pexels"})
    dept.decide(
        f"b-roll asignado a {package.package_id} ({len(assets)} clips Pexels, "
        f"{len(set(a.source for a in assets))} únicos)",
        context_refs=[f"package:{package.package_id}"]
        + [f"{a.source} → escena {a.scene_index}" for a in assets],
        correlation_id=package.package_id,
    )
    return package.model_copy(update={"assets": list(package.assets) + assets})
