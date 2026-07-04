"""Esquema y loader de `tenants/<slug>/integrations.yaml` (F5.3 VER-IA).

Declara QUÉ integraciones usa el tenant y DÓNDE vive cada secret — nunca el
secret en sí (los valores viven en Key Vault o en app settings; este archivo
sí va a git). Lo consumen:

- `ops/provision_tenant.py`: crea el Key Vault del tenant, siembra
  placeholders y configura los app settings como referencias
  @Microsoft.KeyVault (fuente `keyvault`) o los deja como pendientes
  manuales (fuente `env`).
- `connectors/factory.py` (fase siguiente): construir solo los conectores
  de las integraciones declaradas.

El archivo es OPCIONAL: sin él, el provisioner lista todos los secrets como
pendientes manuales (comportamiento previo a F5.3).
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TENANTS_DIR = Path(__file__).resolve().parent.parent.parent / "tenants"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretRef(_Strict):
    """Dónde vive UN secret. Exactamente una fuente:
    - keyvault: nombre del secret en el Key Vault del tenant (el
      provisioner lo siembra y cablea la referencia al app setting).
    - env: se carga a mano como app setting (p.ej. MSAL_CACHE_B64, que
      sale de un device-code flow y no puede pre-crearse).
    """

    keyvault: str | None = None
    env: str | None = None

    @model_validator(mode="after")
    def _exactamente_una_fuente(self) -> "SecretRef":
        if bool(self.keyvault) == bool(self.env):
            raise ValueError(
                "cada secret declara exactamente UNA fuente: keyvault o env"
            )
        return self


class Integration(_Strict):
    provider: str | None = None      # "contifico" | "hubspot" | "graph" | "anthropic" | ...
    secrets: dict[str, SecretRef] = Field(default_factory=dict)  # APP_SETTING -> fuente


class IntegrationsConfig(_Strict):
    """Forma de integrations.yaml. Secciones opcionales por capacidad."""

    erp: Integration | None = None
    crm: Integration | None = None
    mail: Integration | None = None
    ai: Integration | None = None
    prospecting: Integration | None = None
    wordpress: Integration | None = None

    def all_secrets(self) -> dict[str, SecretRef]:
        """{APP_SETTING: SecretRef} de todas las secciones declaradas."""
        out: dict[str, SecretRef] = {}
        for section in (self.erp, self.crm, self.mail, self.ai,
                        self.prospecting, self.wordpress):
            if section:
                out.update(section.secrets)
        return out


def load_tenant_integrations(
    slug: str, tenants_dir: Path | None = None
) -> IntegrationsConfig | None:
    """Carga integrations.yaml del tenant. None si el archivo no existe
    (integraciones sin declarar = todos los secrets son pendientes manuales)."""
    base = tenants_dir or TENANTS_DIR
    path = base / slug / "integrations.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return IntegrationsConfig(**data)
