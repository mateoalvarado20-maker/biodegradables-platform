"""Contexto de marca del tenant (directriz #11: datos del cliente en tenants/).

`tenants/<slug>/marketing.yaml` declara `brand_context_file` (ruta relativa a
la raíz del repo). El Guionista recibe el TEXTO — este módulo es el único que
sabe de dónde sale.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).parent.parent


def load_brand_context(tenant_slug: str, base_dir: str | Path | None = None) -> str:
    """`base_dir` es el directorio de tenants (default: <repo>/tenants). El
    brand_context_file se resuelve relativo al padre de ese directorio."""
    tenants_dir = Path(base_dir) if base_dir is not None else _REPO_ROOT / "tenants"
    cfg_path = tenants_dir / tenant_slug / "marketing.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(f"el tenant {tenant_slug!r} no tiene marketing.yaml")
    with open(cfg_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rel = data.get("brand_context_file")
    if not rel:
        raise KeyError(f"{cfg_path} no declara brand_context_file")
    ctx_path = tenants_dir.parent / rel
    if not ctx_path.exists():
        raise FileNotFoundError(f"brand_context_file no existe: {ctx_path}")
    return ctx_path.read_text(encoding="utf-8")
