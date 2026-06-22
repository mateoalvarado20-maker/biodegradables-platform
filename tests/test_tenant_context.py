"""Tests del andamiaje multiempresa (Acciones 1+4).

NO tocan los bots/agentes en producción: solo ejercitan el paquete nuevo `core/`.
"""
from __future__ import annotations

import pytest

from core.config.loader import load_tenant_config
from core.config.schema import TenantConfig, parse_hhmm
from core.context import TenantContext


def test_load_biodegradables_tenant():
    ctx = TenantContext.load("biodegradables")
    assert ctx.slug == "biodegradables"
    assert ctx.display_name == "Biodegradables Ecuador"
    assert ctx.locale == "es-EC"
    assert ctx.timezone == "America/Guayaquil"
    assert isinstance(ctx.config, TenantConfig)


def test_unknown_tenant_raises_clear_error():
    with pytest.raises(FileNotFoundError):
        TenantContext.load("no-existe-este-tenant")


def test_extra_key_is_rejected(tmp_path):
    # extra='forbid' en el schema => un typo en el YAML falla, no se ignora.
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "config.yaml").write_text(
        "slug: acme\ndisplay_name: Acme\ncampo_con_typo: 1\n", encoding="utf-8"
    )
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        load_tenant_config("acme", tenants_dir=tmp_path)


def test_minimal_valid_tenant(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "config.yaml").write_text(
        "display_name: Acme Corp\n", encoding="utf-8"
    )
    cfg = load_tenant_config("acme", tenants_dir=tmp_path)
    assert cfg.slug == "acme"               # se infiere del nombre de carpeta
    assert cfg.display_name == "Acme Corp"
    assert cfg.commercial.meta_factor == 1.20  # default


def test_parse_hhmm():
    assert parse_hhmm("16:30") == (16, 30)
    assert parse_hhmm("08:00") == (8, 0)
    assert parse_hhmm(None) is None
    assert parse_hhmm("") is None
