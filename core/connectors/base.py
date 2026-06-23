"""Interfaces (Protocols) de los conectores externos. NÚCLEO PURO: sin vendors.

Un módulo de negocio pide `ctx.erp` / `ctx.crm` / `ctx.mail` y programa contra
ESTAS interfaces, nunca contra un cliente concreto. Las implementaciones reales
viven en el paquete top-level `connectors/` (el único lugar donde se nombra a un
proveedor). Así, cambiar el sistema externo de un cliente = otra implementación,
cero cambios en `modules/` ni en `core/`.

Acción 5 (base) del plan multiempresa. Andamiaje aditivo: los bots actuales NO lo
usan todavía. `runtime_checkable` permite afirmar en tests que un adaptador
satisface la interfaz (chequea presencia de métodos).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ErpConnector(Protocol):
    """Contrato mínimo de un ERP (fuente de ventas/cartera)."""

    def ventas_dia(self, fecha: date) -> dict[str, Any]: ...
    def ventas_rango(self, fecha_inicial: date, fecha_final: date) -> dict[str, Any]: ...
    def ventas_por_ciudad(
        self, fecha: date, fecha_final: date | None = None
    ) -> dict[str, Any]: ...
    def cumplimiento_mes(self, fecha_referencia: date | None = None) -> dict[str, Any]: ...


@runtime_checkable
class CrmConnector(Protocol):
    """Contrato mínimo de un CRM (leads/deals/pipeline)."""

    def leads_ayer(self) -> dict: ...
    def leads_promedio_7d(self) -> float: ...
    def deals_ganados_ayer(self) -> dict: ...
    def pipeline_abierto(self) -> dict: ...


@runtime_checkable
class MailConnector(Protocol):
    """Contrato mínimo de envío de correo (passthrough hacia el proveedor)."""

    def send(self, *args: Any, **kwargs: Any) -> Any: ...
