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


# ===== Hardening post-Andex (2026-07-06): providers, startup, consent,
# fallback de región, resumibilidad =====

def test_plan_registra_resource_providers(plan_andex):
    """Las subs nuevas no traen providers — el plan los registra ANTES de
    crear recursos (incidente Andex: az bot create habría fallado)."""
    cmds = plan_andex["comandos"]
    registros = [c for c in cmds if "provider register" in c]
    assert any("Microsoft.Web" in c for c in registros)
    assert any("Microsoft.BotService" in c for c in registros)
    primer_create = next(i for i, c in enumerate(cmds) if " create" in c)
    ultimo_register = max(i for i, c in enumerate(cmds) if "provider register" in c)
    assert ultimo_register < primer_create, "providers deben ir primero"


def test_plan_setea_startup_y_always_on(plan_andex):
    """Sin startup command la webapp da 404 (incidente Andex); sin Always On
    el scheduler se duerme."""
    linea = next(c for c in plan_andex["comandos"] if "--startup-file" in c)
    assert "gunicorn" in linea and "teams_bot:app" in linea
    assert "--always-on true" in linea


def test_plan_otorga_admin_consent(plan_andex):
    """El consent se otorga vía appRoleAssignments de Graph, sin paso
    interactivo del cliente."""
    cmds = "\n".join(plan_andex["comandos"])
    assert cmds.count("ad sp create") == 2          # un SP por app
    assert cmds.count("appRoleAssignments") == 4    # 2 roles × 2 apps


def test_no_grant_consent_lo_omite(prov):
    r = prov.provision(
        "andex", location="eastus2", resource_group=None,
        plan_sku="B1", expected_tenant_id="11111111-2222-3333-4444-555555555555",
        dry_run=True, grant_consent=False,
    )
    assert "appRoleAssignments" not in "\n".join(r["comandos"])
    assert r["consent_otorgado"] is False
    assert r["admin_consent_url"].startswith("https://login.microsoftonline.com/")


def test_fallback_de_region_por_cuota(prov, monkeypatch):
    """Si la región pedida no tiene cuota de VMs (subs nuevas), prueba las
    de fallback en orden y devuelve la efectiva."""
    plan = prov.Plan(dry_run=False)
    intentos = []

    def fake_az(*args, **kwargs):
        loc = args[args.index("-l") + 1]
        intentos.append(loc)
        if loc in ("eastus2", "centralus"):
            raise SystemExit("az falló (1): Operation cannot be completed "
                             "without additional quota. Total VMs: 0")
        return None

    monkeypatch.setattr(plan, "az", fake_az)
    monkeypatch.setattr(plan, "existente", lambda *a: None)
    efectiva = prov._crear_plan_con_fallback(plan, "rg-x", "plan-x",
                                             "eastus2", "B1")
    assert intentos == ["eastus2", "centralus", "eastus"]
    assert efectiva == "eastus"


def test_fallback_no_traga_errores_ajenos(prov, monkeypatch):
    """Un fallo que NO es de cuota (permiso, nombre inválido…) aborta en el
    primer intento — no hay que enmascararlo probando regiones."""
    plan = prov.Plan(dry_run=False)

    def fake_az(*args, **kwargs):
        raise SystemExit("az falló (1): AuthorizationFailed")

    monkeypatch.setattr(plan, "az", fake_az)
    monkeypatch.setattr(plan, "existente", lambda *a: None)
    with pytest.raises(SystemExit, match="AuthorizationFailed"):
        prov._crear_plan_con_fallback(plan, "rg-x", "plan-x", "eastus2", "B1")


def test_resume_reutiliza_plan_existente(prov, monkeypatch):
    """Si el plan ya existe (corrida anterior que falló después), se
    reutiliza su región y NO se intenta crear de nuevo."""
    plan = prov.Plan(dry_run=False)
    monkeypatch.setattr(plan, "existente",
                        lambda *a: {"location": "Central US"})
    monkeypatch.setattr(plan, "az",
                        lambda *a, **k: pytest.fail("no debía crear nada"))
    efectiva = prov._crear_plan_con_fallback(plan, "rg-x", "plan-x",
                                             "eastus2", "B1")
    assert efectiva == "centralus"


def test_dry_run_reporta_region_y_consent(plan_andex):
    assert plan_andex["location_efectiva"] == "eastus2"
    assert plan_andex["consent_otorgado"] is False  # dry-run nunca otorga
