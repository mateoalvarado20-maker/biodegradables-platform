"""Tests F2.4 (VER-IA 2026-07-02): prompts sin marca y parámetros al tenant.

Cubre: identidad outbound (firmante/dominio/website) en el reply agent,
prefijos de documento del ERP, fondo de caja, dashboard URL y keywords de
provincia — todos con defaults legacy idénticos a producción y override
desde el config.yaml del tenant.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def cc():
    import core_config
    return importlib.reload(core_config)


# ---------- Defaults legacy congelados ----------

def test_defaults_legacy(cc):
    assert cc.COMPANY_DOMAIN == "biodegradablesecuador.com"
    assert cc.OUTBOUND_SIGNER_EMAIL == "malvarado@biodegradablesecuador.com"
    assert cc.outbound_signer_name() == "Mateo Alvarado"  # desde PEOPLE
    assert cc.DOC_PREFIXES == {"GYE": "001-001", "UIO": "001-002"}
    assert cc.CAJA_FONDO_DEFAULT == 50.00
    assert cc.CAJA_FONDO_POR_SUCURSAL == {"Guayaquil": 100.00, "Quito": 50.00}
    assert "app.powerbi.com" in cc.DASHBOARD_URL
    assert cc.LOGISTICS_PROVINCIA_KEYWORDS == []


def test_yaml_biodegradables_equivale_a_legacy(cc, monkeypatch):
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "biodegradables")
    import core_config
    y = importlib.reload(core_config)
    assert y.COMPANY_DOMAIN == cc.COMPANY_DOMAIN
    assert y.OUTBOUND_SIGNER_EMAIL == cc.OUTBOUND_SIGNER_EMAIL
    assert y.DOC_PREFIXES == cc.DOC_PREFIXES
    assert y.DASHBOARD_URL == cc.DASHBOARD_URL
    assert y.CAJA_FONDO_DEFAULT == cc.CAJA_FONDO_DEFAULT
    assert y.CAJA_FONDO_POR_SUCURSAL == cc.CAJA_FONDO_POR_SUCURSAL
    monkeypatch.delenv("TENANT_CONFIG_SOURCE")
    monkeypatch.delenv("TENANT_SLUG")
    importlib.reload(core_config)


# ---------- Reply agent: prompt 100% desde config ----------

def test_reply_agent_prompt_usa_identidad_del_tenant(cc, monkeypatch):
    import reply_agent
    reply_agent = importlib.reload(reply_agent)

    monkeypatch.setattr(cc, "COMPANY_NAME", "Andex")
    monkeypatch.setattr(cc, "COMPANY_WEBSITE", "https://andex.example.com/")
    monkeypatch.setattr(cc, "OUTBOUND_SIGNER_EMAIL", "maria@andex.example.com")
    monkeypatch.setitem(
        cc.PEOPLE, "maria@andex.example.com", {"name": "María Pérez"},
    )
    prompt = reply_agent._system_prompt("(contexto)")
    assert "Andex" in prompt
    assert "María Pérez" in prompt
    assert "maria@andex.example.com" in prompt
    assert "andex.example.com" in prompt
    # Nada del tenant #1 se filtra al prompt de otro tenant
    assert "Biodegradables" not in prompt
    assert "Mateo" not in prompt
    assert "biodegradablesecuador" not in prompt


def test_reply_agent_own_domain_desde_config(cc):
    import reply_agent
    reply_agent = importlib.reload(reply_agent)
    assert reply_agent.OWN_DOMAIN == cc.COMPANY_DOMAIN
    # El filtro de internos sigue funcionando con el default
    interno = {"from": {"emailAddress": {"address": "x@biodegradablesecuador.com"}}}
    ok, reason = reply_agent._is_candidate(interno)
    assert ok is False and "interno" in reason


# ---------- Prefijos ERP derivados ----------

def test_contifico_prefijos_desde_config(cc):
    import contifico_client
    contifico_client = importlib.reload(contifico_client)
    assert contifico_client.PREFIJO_GYE == "001-001"
    assert contifico_client.PREFIJO_UIO == "001-002"


def test_logistics_origen_derivado_de_prefijos(cc):
    import daily_logistics_report as dlr
    dlr = importlib.reload(dlr)
    assert dlr.ORIGEN_POR_PREFIJO == {"001-001": "Guayaquil", "001-002": "Quito"}


def test_logistics_provincia_keywords_default_ec(cc):
    import daily_logistics_report as dlr
    dlr = importlib.reload(dlr)
    assert dlr.PROVINCIA_KEYWORDS is dlr.PROVINCIA_KEYWORDS_EC
    assert ("quito", "Pichincha", "Quito") in dlr.PROVINCIA_KEYWORDS


def test_logistics_provincia_keywords_override(cc, monkeypatch):
    monkeypatch.setattr(
        cc, "LOGISTICS_PROVINCIA_KEYWORDS",
        [("bogota", "Cundinamarca", "Bogotá")],
    )
    import daily_logistics_report as dlr
    dlr = importlib.reload(dlr)
    assert dlr.PROVINCIA_KEYWORDS == [("bogota", "Cundinamarca", "Bogotá")]
    importlib.reload(cc)
    importlib.reload(dlr)


# ---------- Fondo de caja ----------

def test_fondo_caja_desde_config(cc, tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    activity_state = importlib.reload(activity_state)
    assert activity_state.get_fondo_caja("Guayaquil") == 100.00
    assert activity_state.get_fondo_caja("Quito") == 50.00
    assert activity_state.get_fondo_caja(None) == 50.00


# ---------- Overrides del YAML (aplicación) ----------

def test_apply_overrides_de_f24(cc):
    """Simula lo que _maybe_load_from_tenant aplica desde el YAML."""
    from core.config.schema import TenantConfig
    cfg = TenantConfig(
        slug="acme", display_name="ACME",
        company={"domain": "acme.com", "website": "https://acme.com/",
                 "outbound_signer": "ana@acme.com"},
        erp={"document_prefixes": {"NORTE": "002-001"}},
        commercial={"dashboard_url": "https://acme.com/dash"},
        caja={"fondo_default": 80.0, "fondo_por_sucursal": {"Norte": 120.0}},
        logistics={"provincia_keywords": [["medellin", "Antioquia", "Medellín"]]},
    )
    assert cfg.company.domain == "acme.com"
    assert cfg.erp.document_prefixes == {"NORTE": "002-001"}
    assert cfg.caja.fondo_default == 80.0
    assert cfg.logistics.provincia_keywords == [("medellin", "Antioquia", "Medellín")]
    assert cfg.commercial.dashboard_url == "https://acme.com/dash"
