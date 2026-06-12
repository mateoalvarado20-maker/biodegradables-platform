"""Cliente Microsoft Graph para inbox + drafts (Outlook).

Reutiliza la autenticación MSAL de pbi_cloud.py (mismo app registration,
mismo token cache). Solo agrega el scope Mail.ReadWrite que ya fue concedido
en Azure el 2026-05-19.

Funciones:
    list_unread_inbox(since_iso)    → lista correos no leídos desde una fecha
    get_message(msg_id)             → trae un correo completo
    get_thread(conversation_id)     → trae todos los mensajes de una conversación
    create_draft_reply(msg_id, body_html, cc=None) → crea borrador de respuesta
"""
from __future__ import annotations

from typing import Any

import httpx

from pbi_cloud import get_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAIL_RW_SCOPES = ["https://graph.microsoft.com/Mail.ReadWrite"]


def _graph_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    params: dict | None = None,
    interactive_ok: bool = True,
) -> dict:
    token = get_token(MAIL_RW_SCOPES, interactive_ok=interactive_ok)
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


def list_unread_inbox(
    since_iso: str | None = None,
    *,
    top: int = 50,
    interactive_ok: bool = True,
) -> list[dict]:
    """Lista correos no leídos del inbox.

    Args:
        since_iso: ISO 8601 UTC (ej. "2026-05-19T10:00:00Z"). Si None, sin filtro de fecha.
        top: máximo de resultados (default 50, Graph permite hasta 999).
    """
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
    data = _graph_request(
        "GET",
        "/me/mailFolders/inbox/messages",
        params=params,
        interactive_ok=interactive_ok,
    )
    return data.get("value", [])


def get_message(msg_id: str, *, interactive_ok: bool = True) -> dict:
    """Trae un correo completo incluyendo body."""
    params = {
        "$select": (
            "id,conversationId,subject,from,toRecipients,ccRecipients,"
            "receivedDateTime,body,bodyPreview,internetMessageId"
        ),
    }
    return _graph_request(
        "GET",
        f"/me/messages/{msg_id}",
        params=params,
        interactive_ok=interactive_ok,
    )


def get_thread(conversation_id: str, *, interactive_ok: bool = True) -> list[dict]:
    """Trae todos los mensajes de una conversación, ordenados cronológicamente.

    Útil para que el agente vea el historial antes de responder.
    """
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$select": (
            "id,subject,from,toRecipients,receivedDateTime,bodyPreview,body"
        ),
        "$orderby": "receivedDateTime asc",
        "$top": "50",
    }
    data = _graph_request(
        "GET",
        "/me/messages",
        params=params,
        interactive_ok=interactive_ok,
    )
    return data.get("value", [])


def create_draft_reply(
    msg_id: str,
    body_html: str,
    *,
    cc: list[str] | None = None,
    interactive_ok: bool = True,
) -> dict:
    """Crea un borrador de respuesta al correo `msg_id`.

    Graph inserta automáticamente el texto original como quoted reply debajo
    del body que pasamos. El borrador queda en la carpeta Drafts de Outlook
    y aparece enhebrado con la conversación original.

    Returns:
        dict con el mensaje creado (incluye 'id' del draft, 'webLink' para abrirlo).
    """
    body: dict[str, Any] = {
        "message": {
            "body": {
                "contentType": "HTML",
                "content": body_html,
            },
        },
    }
    if cc:
        body["message"]["ccRecipients"] = [
            {"emailAddress": {"address": a}} for a in cc
        ]
    return _graph_request(
        "POST",
        f"/me/messages/{msg_id}/createReply",
        json_body=body,
        interactive_ok=interactive_ok,
    )


if __name__ == "__main__":
    # Smoke test: dispara device-code para el nuevo scope Mail.ReadWrite y lista 5 correos.
    print("Autenticando contra Microsoft Graph (Mail.ReadWrite)...")
    get_token(MAIL_RW_SCOPES)
    print("OK - token Mail.ReadWrite obtenido.\n")
    print("Últimos 5 no leídos del inbox:")
    msgs = list_unread_inbox(top=5)
    for m in msgs:
        sender = m.get("from", {}).get("emailAddress", {}).get("address", "?")
        subj = m.get("subject", "(sin asunto)")
        when = m.get("receivedDateTime", "")
        print(f"  - [{when}] {sender}: {subj}")
    print(f"\nTotal no leídos mostrados: {len(msgs)}")
