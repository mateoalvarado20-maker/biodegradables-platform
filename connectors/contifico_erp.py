"""Adaptador ERP para Contifico — implementa core.connectors.base.ErpConnector.

Envuelve el módulo `contifico_client` existente por delegación; NO lo modifica.
El cliente subyacente es inyectable (`client=`) para testear sin red.
"""
from __future__ import annotations

from datetime import date
from typing import Any


class ContificoErp:
    def __init__(self, client: Any = None):
        if client is None:
            import contifico_client

            client = contifico_client
        self._c = client

    def ventas_dia(self, fecha: date) -> dict[str, Any]:
        return self._c.ventas_dia(fecha)

    def ventas_rango(self, fecha_inicial: date, fecha_final: date) -> dict[str, Any]:
        return self._c.ventas_rango(fecha_inicial, fecha_final)

    def ventas_por_ciudad(
        self, fecha: date, fecha_final: date | None = None
    ) -> dict[str, Any]:
        return self._c.ventas_por_ciudad(fecha, fecha_final)

    def cumplimiento_mes(self, fecha_referencia: date | None = None) -> dict[str, Any]:
        return self._c.cumplimiento_mes(fecha_referencia)
