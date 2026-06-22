"""Carga y valida el `config.yaml` de un tenant, con overrides por env var.

Mantiene la MISMA semántica de overrides que `core_config.py` (mismas env vars)
para que `load_tenant_config('biodegradables')` produzca exactamente los valores
que hoy usan los bots — esa equivalencia la fija `test_tenant_config_biodegradables.py`.

Andamiaje aditivo (Acciones 1+3, F0/F1): todavía NO lo importa ningún bot/agente.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from .schema import TenantConfig

# tenants/ vive en la raíz del repo: core/config/loader.py -> ../../../tenants
TENANTS_DIR = Path(__file__).resolve().parents[2] / "tenants"


def _env_list(name: str, current: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return current
    return [e.strip() for e in raw.split(",") if e.strip()]


def _apply_env_overrides(cfg: TenantConfig) -> TenantConfig:
    """Aplica las mismas env vars que core_config (fidelidad con producción)."""
    r = cfg.recipients
    r.commercial_report = _env_list("REPORT_COMERCIAL_TO", r.commercial_report)
    cc = os.environ.get("REPORT_CC")
    if cc is not None:
        r.commercial_report_cc = [cc.strip()]
    logi = os.environ.get("REPORT_LOGISTICA_TO")
    if logi is not None:
        r.logistics_report = [logi.strip()]
    r.calendar_sync_users = _env_list("CALENDAR_SYNC_USERS", r.calendar_sync_users)

    mf = os.environ.get("META_FACTOR")
    if mf is not None:
        cfg.commercial.meta_factor = float(mf)

    cfg.checkin.oficina.users = _env_list(
        "CHECKIN_OFICINA_USERS", cfg.checkin.oficina.users
    )
    cfg.checkin.sucursales.users = _env_list(
        "CHECKIN_SUCURSALES_USERS", cfg.checkin.sucursales.users
    )
    return cfg


def load_tenant_config(slug: str, tenants_dir: Path | None = None) -> TenantConfig:
    """Lee tenants/<slug>/config.yaml, valida contra el schema y aplica env overrides."""
    base = Path(tenants_dir) if tenants_dir else TENANTS_DIR
    path = base / slug / "config.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el config del tenant '{slug}'. Esperado en: {path}"
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw.setdefault("slug", slug)
    cfg = TenantConfig.model_validate(raw)
    return _apply_env_overrides(cfg)
