"""Cliente de Microsoft Graph para envío de correo con auth app-only.

Diseñado para correr en Azure Functions sin device-code flow. Lee env vars:
- GRAPH_TENANT_ID
- GRAPH_CLIENT_ID
- GRAPH_CLIENT_SECRET (resuelto via Key Vault reference)

El correo se envía AS `SENDER` (por defecto malvarado@biodegradablesecuador.com)
usando el endpoint `/users/{sender}/sendMail`. Requiere permiso de aplicación
`Mail.Send` en el tenant (con admin consent).
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

DEFAULT_SENDER = "malvarado@biodegradablesecuador.com"

_TOKEN_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0}


def _get_token() -> str:
    """Obtiene un access token via client_credentials. Cachea en memoria."""
    now = time.time()
    if _TOKEN_CACHE["token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["token"]

    tenant = os.environ["GRAPH_TENANT_ID"]
    client_id = os.environ["GRAPH_CLIENT_ID"]
    client_secret = os.environ["GRAPH_CLIENT_SECRET"]

    r = httpx.post(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(
            f"Graph token request failed {r.status_code}: {r.text[:300]}"
        )
    data = r.json()
    _TOKEN_CACHE["token"] = data["access_token"]
    _TOKEN_CACHE["expires_at"] = now + data["expires_in"]
    return data["access_token"]


def send_email(
    to: str | list[str],
    subject: str,
    body_html: str,
    *,
    cc: str | list[str] | None = None,
    sender: str = DEFAULT_SENDER,
    inline_images: list[dict] | None = None,
) -> None:
    """Envía un correo HTML via Microsoft Graph (app-only auth).

    Args:
        to: destinatario(s).
        subject: asunto.
        body_html: cuerpo HTML.
        cc: copia(s) opcional.
        sender: buzón AS del cual se envía. Requiere permiso en el tenant.
        inline_images: lista de dicts con keys `name`, `content_bytes`,
            `content_id` (referenciado en el HTML como `cid:<content_id>`) y
            opcional `content_type` (default `image/png`).
    """
    import base64

    token = _get_token()
    to_list = [to] if isinstance(to, str) else list(to)
    cc_list: list[str] = []
    if cc:
        cc_list = [cc] if isinstance(cc, str) else list(cc)

    payload: dict[str, Any] = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_list],
        },
        "saveToSentItems": True,
    }
    if cc_list:
        payload["message"]["ccRecipients"] = [
            {"emailAddress": {"address": a}} for a in cc_list
        ]
    if inline_images:
        payload["message"]["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": img["name"],
                "contentType": img.get("content_type", "image/png"),
                "contentBytes": base64.b64encode(img["content_bytes"]).decode(),
                "contentId": img["content_id"],
                "isInline": True,
            }
            for img in inline_images
        ]

    r = httpx.post(
        f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 202):
        raise RuntimeError(f"sendMail falló {r.status_code}: {r.text[:500]}")
