"""TenantContext — el objeto que resuelve "todo lo de un cliente" en un solo lugar.

Hoy (Acción 1 / Fase 0) carga la config declarativa de `tenants/<slug>/` y la
expone. En fases siguientes sumará: secretos (Key Vault), conectores (ERP/CRM/Mail)
y el registro de módulos activos. El runtime arrancará con
`TenantContext.load(os.environ["TENANT_SLUG"])` y todo lo específico del cliente
saldrá de aquí — el código nunca vuelve a nombrar a una empresa.

Andamiaje aditivo: NO se importa todavía desde los bots/agentes en producción.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config.loader import load_tenant_config
from .config.schema import TenantConfig


@dataclass(frozen=True)
class TenantContext:
    slug: str
    config: TenantConfig

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def locale(self) -> str:
        return self.config.locale

    @property
    def timezone(self) -> str:
        return self.config.timezone

    @classmethod
    def load(cls, slug: str, tenants_dir: Path | None = None) -> "TenantContext":
        cfg = load_tenant_config(slug, tenants_dir)
        return cls(slug=slug, config=cfg)
