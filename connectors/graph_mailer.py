"""Adaptador de correo para Microsoft Graph — implementa MailConnector.

Envuelve el módulo `graph_mail` existente por delegación; NO lo modifica.
El cliente subyacente es inyectable (`client=`) para testear sin red.
"""
from __future__ import annotations

from typing import Any


class GraphMailer:
    def __init__(self, client: Any = None):
        if client is None:
            import graph_mail

            client = graph_mail
        self._c = client

    def send(self, *args: Any, **kwargs: Any) -> Any:
        return self._c.send(*args, **kwargs)
