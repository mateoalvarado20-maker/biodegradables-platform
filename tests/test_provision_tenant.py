"""Tests F5.2 (VER-IA 2026-07-04): aprovisionador de tenants (dry-run).

El plan completo (cada comando az) se valida SIN tocar Azure. La ejecución
en vivo se probará contra el tenant M365 de VER-IA cuando exista (F1).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def prov():
    spec = importlib.util.spec_from_file_location(
        "provision_tenant", ROOT / "ops" / "provision_tenant.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def plan_andex(prov):
    return prov.provision(
        "andex", location="eastus2", resource_group=None,
        plan_sku="B1", expected_tenant_id="11111111-2222-3333-4444-555555555555",
        dry_run=True,
    )


def test_dry_run_no_ejecuta_nada(plan_andex):
    """En dry-run el resultado trae el plan pero ningún recurso real."""
    assert plan_andex["admin_api_token"] == "<generado en vivo>"
    assert all(c.startswith("az ") for c in plan_andex["comandos"])


def test_plan_cubre_las_4_etapas(plan_andex):
    cmds = "\n".join(plan_andex["comandos"])
    # 1) App Registrations (x2) con permisos Graph
    assert cmds.count("ad app create") == 2
    assert "Andex — Data Bot" in cmds and "Andex — Activities Bot" in cmds
    assert "required-resource-accesses" in cmds
    assert cmds.count("ad app credential reset") == 2
    # 2) Azure Bots (x2) SingleTenant + canal Teams
    assert cmds.count("bot create") == 2
    assert cmds.count("bot msteams create") == 2
    assert "/api/messages" in cmds and "/api/activities/messages" in cmds
    # 3) App Service plan + webapp Linux/Python + settings
    assert "appservice plan create" in cmds and "--is-linux" in cmds
    assert "PYTHON:3.12" in cmds
    assert "webapp config appsettings set" in cmds
    # 4) nombres derivados del slug
    assert plan_andex["resource_group"] == "rg-andex-prod"
    assert plan_andex["webapp"] == "andex-veria-app"


def test_settings_sensibles_no_se_imprimen(plan_andex):
    """En el plan, los settings muestran el NOMBRE pero enmascaran el VALOR
    (ahí viajan el token admin y los secrets de los bots)."""
    import re
    linea = next(c for c in plan_andex["comandos"]
                 if "appsettings set" in c)
    assert "ADMIN_API_TOKEN=***" in linea
    assert "MICROSOFT_APP_PASSWORD=***" in linea
    # Ningún valor real: nada con pinta de token largo tras un '='
    assert not re.search(r"=[A-Za-z0-9+/]{24,}", linea), linea


def test_salidas_para_el_operador(plan_andex):
    assert plan_andex["admin_consent_url"].startswith(
        "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555/adminconsent"
    )
    assert plan_andex["siguiente_gen_teams_app"].startswith(
        "python ops/gen_teams_app.py andex"
    )
    assert "ANTHROPIC_API_KEY" in plan_andex["secrets_pendientes"]
    assert "az webapp deploy" in plan_andex["deploy"]


def test_app_settings_derivados_del_yaml(prov):
    from core.config.loader import load_tenant_config
    cfg = load_tenant_config("andex")
    s = prov._app_settings("andex", cfg, "tok")
    assert s["TENANT_SLUG"] == "andex"
    assert s["TENANT_CONFIG_SOURCE"] == "yaml"
    assert s["STATE_DIR"] == "/home/.claude-agent"
    assert s["ADMIN_API_TOKEN"] == "tok"
