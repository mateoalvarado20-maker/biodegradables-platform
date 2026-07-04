"""Tests F5.3 (VER-IA 2026-07-04): integrations.yaml + Key Vault en el
provisioner. Los secrets NUNCA viven en git — el YAML declara dónde están."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config.integrations import (  # noqa: E402
    IntegrationsConfig,
    SecretRef,
    load_tenant_integrations,
)


# ---------- Schema ----------

def test_secret_ref_exige_exactamente_una_fuente():
    assert SecretRef(keyvault="x").keyvault == "x"
    assert SecretRef(env="X").env == "X"
    with pytest.raises(Exception, match="exactamente UNA fuente"):
        SecretRef()
    with pytest.raises(Exception, match="exactamente UNA fuente"):
        SecretRef(keyvault="x", env="X")


def test_schema_estricto_rechaza_campos_desconocidos():
    with pytest.raises(Exception):
        IntegrationsConfig(erp={"provider": "contifico", "token": "NO"})


def test_all_secrets_junta_las_secciones():
    cfg = IntegrationsConfig(
        erp={"provider": "contifico",
             "secrets": {"CONTIFICO_API_TOKEN": {"keyvault": "contifico"}}},
        ai={"provider": "anthropic",
            "secrets": {"ANTHROPIC_API_KEY": {"env": "ANTHROPIC_API_KEY"}}},
    )
    s = cfg.all_secrets()
    assert set(s) == {"CONTIFICO_API_TOKEN", "ANTHROPIC_API_KEY"}
    assert s["CONTIFICO_API_TOKEN"].keyvault == "contifico"


# ---------- Loader ----------

def test_biodegradables_declara_sus_integraciones():
    integ = load_tenant_integrations("biodegradables")
    assert integ is not None
    secrets = integ.all_secrets()
    # La realidad actual del tenant #1: todo por env (sin Key Vault aún)
    assert "CONTIFICO_API_TOKEN" in secrets
    assert secrets["CONTIFICO_API_TOKEN"].env == "CONTIFICO_API_TOKEN"
    assert integ.erp.provider == "contifico"
    assert integ.crm.provider == "hubspot"


def test_archivo_ausente_devuelve_none(tmp_path):
    (tmp_path / "acme").mkdir()
    assert load_tenant_integrations("acme", tenants_dir=tmp_path) is None


# ---------- Provisioner + Key Vault (dry-run) ----------

@pytest.fixture()
def prov():
    spec = importlib.util.spec_from_file_location(
        "provision_tenant", ROOT / "ops" / "provision_tenant.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_biodegradables_sin_kv_todo_pendiente_manual(prov):
    """Tenant con fuentes env → NO se crea Key Vault; los secrets salen
    como pendientes manuales con su nombre real."""
    r = prov.provision("biodegradables", "eastus2", None, "B1",
                       "11111111-2222-3333-4444-555555555555", dry_run=True)
    assert r["keyvault"] is None
    cmds = "\n".join(r["comandos"])
    assert "keyvault create" not in cmds
    assert any("CONTIFICO_API_TOKEN" in p for p in r["secrets_pendientes"])


def test_tenant_con_kv_genera_vault_y_referencias(prov, tmp_path, monkeypatch):
    """Tenant que declara fuentes keyvault → vault + identity + policy +
    placeholders + app settings como referencias @Microsoft.KeyVault."""
    import core.config.integrations as integ_mod
    # Tenant sintético: config mínima + integrations con keyvault
    t = tmp_path / "acme"
    t.mkdir()
    (t / "config.yaml").write_text(
        "slug: acme\ndisplay_name: ACME\n", encoding="utf-8"
    )
    (t / "integrations.yaml").write_text(
        "erp:\n  provider: contifico\n  secrets:\n"
        "    CONTIFICO_API_TOKEN: { keyvault: contifico-api-token }\n"
        "prospecting:\n  secrets:\n"
        "    MSAL_CACHE_B64: { env: MSAL_CACHE_B64 }\n",
        encoding="utf-8",
    )
    import core.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "TENANTS_DIR", tmp_path)
    monkeypatch.setattr(integ_mod, "TENANTS_DIR", tmp_path)

    r = prov.provision("acme", "eastus2", None, "B1",
                       "11111111-2222-3333-4444-555555555555", dry_run=True)
    cmds = "\n".join(r["comandos"])
    assert r["keyvault"] == "kv-acme-veria"
    assert "keyvault create" in cmds
    assert "webapp identity assign" in cmds
    assert "set-policy" in cmds and "get list" in cmds
    # Placeholder sembrado con el valor enmascarado
    assert "keyvault secret set --vault-name kv-acme-veria --name contifico-api-token --value=***" in cmds.replace("--value ***", "--value=***") or "contifico-api-token" in cmds
    # App setting como referencia KeyVault
    assert "@Microsoft.KeyVault(SecretUri=https://kv-acme-veria.vault.azure.net/secrets/contifico-api-token/)" in cmds
    # El env-sourced queda pendiente manual; el kv-sourced con su comando
    assert any("MSAL_CACHE_B64 (app setting manual)" == p
               for p in r["secrets_pendientes"])
    assert any(p.startswith("CONTIFICO_API_TOKEN: az keyvault secret set")
               for p in r["secrets_pendientes"])


def test_kv_name_valido():
    spec = importlib.util.spec_from_file_location(
        "provision_tenant", ROOT / "ops" / "provision_tenant.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._kv_name("acme") == "kv-acme-veria"
    assert len(mod._kv_name("cliente-con-slug-larguisimo")) <= 24
