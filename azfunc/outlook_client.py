"""Cliente Microsoft Graph para inbox + drafts de Outlook (versión Azure).

Usa autenticación app-only (client_credentials) — el token se obtiene
reutilizando `_get_token()` de `graph_mail.py`. Como app-only NO soporta
`/me/...`, todas las llamadas van contra `/users/{MAILBOX}/...`.

El buzón target es Mateo (`malvarado@biodegradablesecuador.com`). Si en el
futuro hay que cambiar, ajustar `MAILBOX`.
"""
from __future__ import annotations

from typing import Any

import httpx

from graph_mail import _get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAILBOX = "malvarado@biodegradablesecuador.com"


def _graph_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict:
    token = _get_token()
    r = httpx.request(
        method,
        f"{GRAPH_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json_body,
        params=params,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Graph {method} {path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def list_unread_inbox(since_iso: str | None = None, *, top: int = 50) -> list[dict]:
    filters = ["isRead eq false"]
    if since_iso:
        filters.append(f"receivedDateTime ge {since_iso}")
    params = {
        "$filter": " and ".join(filters),
        "$top": str(top),
        "$select": (
            "id,conversationId,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,bodyPreview,isRead,hasAttachments,internetMessageId"
        ),
        "$orderby": "receivedDateTime desc",
    }
    data = _graph_request("GET", f"/users/{MAILBOX}/mailFolders/inbox/messages", params=params)
    return data.get("value", [])


def get_message(msg_id: str) -> dict:
    params = {
        "$select": (
            "id,conversationId,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,body,bodyPreview,internetMessageId"
        ),
    }
    return _graph_request("GET", f"/users/{MAILBOX}/messages/{msg_id}", params=params)


def get_thread(conversation_id: str) -> list[dict]:
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$select": "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body",
        "$orderby": "receivedDateTime asc",
        "$top": "50",
    }
    data = _graph_request("GET", f"/users/{MAILBOX}/messages", params=params)
    return data.get("value", [])


def create_draft_reply(
    msg_id: str, body_html: str, *, cc: list[str] | None = None
) -> dict:
    body: dict[str, Any] = {
        "message": {"body": {"contentType": "HTML", "content": body_html}}
    }
    if cc:
        body["message"]["ccRecipients"] = [
            {"emailAddress": {"address": a}} for a in cc
        ]
    return _graph_request(
        "POST", f"/users/{MAILBOX}/messages/{msg_id}/createReply", json_body=body
    )
