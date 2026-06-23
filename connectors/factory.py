"""Factory de conectores: dado el proveedor configurado, devuelve la implementación.

Hoy mapea el nombre del proveedor a su adaptador. En la fase siguiente leerá
`tenants/<slug>/integrations.yaml` para construir solo los conectores que los
módulos activos del tenant necesitan. Si el proveedor no tiene adaptador, FALLA
con un mensaje claro (no silenciosamente) — clave para el onboarding.
"""
from __future__ import annotations

from typing import Any

from core.connectors.base import CrmConnector, ErpConnector, MailConnector


def build_erp(provider: str, **kwargs: Any) -> ErpConnector:
    if provider == "contifico":
        from connectors.contifico_erp import ContificoErp

        return ContificoErp(**kwargs)
    raise ValueError(
        f"ERP no soportado: {provider!r}. Agregá un adaptador en connectors/ "
        "que implemente core.connectors.base.ErpConnector."
    )


def build_crm(provider: str, **kwargs: Any) -> CrmConnector:
    if provider == "hubspot":
        from connectors.hubspot_crm import HubspotCrm

        return HubspotCrm(**kwargs)
    raise ValueError(f"CRM no soportado: {provider!r}. Agregá un adaptador en connectors/.")


def build_mail(provider: str, **kwargs: Any) -> MailConnector:
    if provider == "graph":
        from connectors.graph_mailer import GraphMailer

        return GraphMailer(**kwargs)
    raise ValueError(f"Mail no soportado: {provider!r}. Agregá un adaptador en connectors/.")
