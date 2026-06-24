"""Fase 5 — el preflight go/no-go del entorno demo.

Verifica que demo_preflight.checks() pasa bajo un entorno Andex bien configurado
y FALLA (fail-closed) cuando algo apunta al cliente real o fuera del dominio demo.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_with(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    importlib.reload(importlib.import_module("core_config"))
    importlib.reload(importlib.import_module("ask_agent"))
    return importlib.reload(importlib.import_module("demo_preflight"))


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    yield
    monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    importlib.reload(importlib.import_module("core_config"))
    importlib.reload(importlib.import_module("ask_agent"))


def _andex_env():
    return dict(
        DEMO_MODE="1", TENANT_CONFIG_SOURCE="yaml", TENANT_SLUG="andex",
        DEMO_EMAIL_DOMAIN="andexdemo.com", DEMO_EMAIL_TO="demo@andexdemo.com",
        DEMO_FROM_USER="amora@andexdemo.com",
        BOT_ALLOWED_USERS_DATA="rsalinas@andexdemo.com,cvega@andexdemo.com",
        CONTIFICO_API_TOKEN="x", HUBSPOT_TOKEN="x",
    )


def test_preflight_ok_para_andex(monkeypatch):
    pf = _reload_with(monkeypatch, **_andex_env())
    results = pf.checks()
    fails = [r for r in results if not r["ok"]]
    assert fails == [], f"checks fallidos: {fails}"


def test_preflight_falla_si_tenant_real(monkeypatch):
    env = _andex_env()
    env["TENANT_SLUG"] = "biodegradables"
    pf = _reload_with(monkeypatch, **env)
    results = pf.checks()
    assert any(not r["ok"] for r in results)


def test_preflight_falla_si_correo_fuera_de_dominio(monkeypatch):
    env = _andex_env()
    env["DEMO_EMAIL_TO"] = "dsanchez@biodegradablesecuador.com"  # dominio real
    pf = _reload_with(monkeypatch, **env)
    results = pf.checks()
    assert any(not r["ok"] for r in results)


def test_manifests_demo_existen_y_son_validos():
    import json
    base = ROOT / "tenants" / "andex" / "teams"
    for fn in ("manifest_data.json", "manifest_activities.json"):
        data = json.loads((base / fn).read_text(encoding="utf-8"))
        assert data["accentColor"] == "#0B6E99"
        assert "andexdemo.com" in data["developer"]["websiteUrl"]
        # los IDs siguen siendo placeholders (se completan al provisionar)
        assert "REEMPLAZAR" in data["id"]
