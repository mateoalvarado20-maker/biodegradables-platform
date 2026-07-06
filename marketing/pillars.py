"""Pilares de contenido del tenant (hipótesis gestionadas por datos).

Los pilares viven en `tenants/<slug>/marketing.yaml` (dato del cliente, nunca
hardcodeado en el módulo — regla de pureza de la plataforma). Nacen como
`hypothesis` aprobadas por el board; el Analista (F3) los promueve a
`validated` (con exp_ids) o los `retired` cuando los datos lo demuestren.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from marketing.models import Pillar

_REPO_ROOT = Path(__file__).parent.parent


def load_pillars(tenant_slug: str, base_dir: str | Path | None = None) -> list[Pillar]:
    base = Path(base_dir) if base_dir is not None else _REPO_ROOT / "tenants"
    path = base / tenant_slug / "marketing.yaml"
    if not path.exists():
        raise FileNotFoundError(f"el tenant {tenant_slug!r} no tiene marketing.yaml en {base}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    raw = data.get("content_pillars") or []
    pillars = [Pillar(**p) for p in raw]
    ids = [p.id for p in pillars]
    if len(ids) != len(set(ids)):
        raise ValueError(f"pilares duplicados en {path}: {ids}")
    return pillars


def active_pillars(tenant_slug: str, base_dir: str | Path | None = None) -> list[Pillar]:
    """Los pilares sobre los que el Planificador puede trabajar hoy."""
    return [p for p in load_pillars(tenant_slug, base_dir) if p.status != "retired"]
