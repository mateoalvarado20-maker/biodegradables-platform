"""Tests del motor de prompts por capas (Acción 6). No tocan los bots en producción."""
from __future__ import annotations

import pytest

from core.context import TenantContext
from core.prompts import engine


def test_substitute_and_missing_var():
    assert engine.substitute("Hola {{name}}", {"name": "Acme"}) == "Hola Acme"
    with pytest.raises(KeyError):
        engine.substitute("Hola {{falta}}", {})


def test_parse_frontmatter():
    meta, body = engine.parse_frontmatter("---\nversion: 2\nrole: data_bot\n---\nCuerpo")
    assert meta["version"] == "2"
    assert body.strip() == "Cuerpo"
    meta2, body2 = engine.parse_frontmatter("Sin frontmatter")
    assert meta2 == {}
    assert body2 == "Sin frontmatter"


def test_render_layers_compose_and_strip_frontmatter():
    out = engine.render(
        base="---\nversion: 1\n---\nBASE {{display_name}}",
        context="CONTEXTO",
        overlay="OVERLAY",
        variables={"display_name": "Acme"},
    )
    assert "BASE Acme" in out
    assert "CONTEXTO" in out
    assert "OVERLAY" in out
    assert "version" not in out  # el frontmatter no se filtra al prompt final


def test_build_system_prompt_for_biodegradables():
    ctx = TenantContext.load("biodegradables")
    p = engine.build_system_prompt("data_bot", ctx)
    assert "Biodegradables Ecuador" in p   # {{display_name}} sustituido
    assert "{{" not in p                   # no quedan variables sin resolver


def test_prompt_version_exposed_for_audit():
    assert engine.prompt_version("data_bot") == "1"
    assert engine.prompt_version("activities_bot") == "1"


def test_unknown_role_fails_clear():
    ctx = TenantContext.load("biodegradables")
    with pytest.raises(FileNotFoundError):
        engine.build_system_prompt("rol-inexistente", ctx)
