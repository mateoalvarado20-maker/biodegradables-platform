"""Adaptador CRM para HubSpot — implementa core.connectors.base.CrmConnector.

Envuelve el módulo `hubspot_client` existente por delegación; NO lo modifica.
El cliente subyacente es inyectable (`client=`) para testear sin red.
"""
from __future__ import annotations

from typing import Any


class HubspotCrm:
    def __init__(self, client: Any = None):
        if client is None:
            import hubspot_client

            client = hubspot_client
        self._c = client

    def leads_ayer(self) -> dict:
        return self._c.leads_ayer()

    def leads_promedio_7d(self) -> float:
        return self._c.leads_promedio_7d()

    def deals_ganados_ayer(self) -> dict:
        return self._c.deals_ganados_ayer()

    def pipeline_abierto(self) -> dict:
        return self._c.pipeline_abierto()
