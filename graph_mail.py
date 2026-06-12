"""Cliente de Microsoft Graph para enviar correo desde Azure App Service.

Usa OAuth2 client_credentials flow (Service Principal) con la App Registration
del bot — NO requiere MSAL cache local. Funciona desde cualquier servidor que
tenga `MICROSOFT_APP_ID` + `MICROSOFT_APP_PASSWORD` + `MICROSOFT_APP_TENANT_ID`.

Requiere que la app tenga el permiso de aplicación `Mail.Send` con admin consent
(ya está concedido en `biodegradables-data-bot`).

Uso:
    import graph_mail
    graph_mail.send(
        from_user="malvarado@biodegradablesecuador.com",
        to=["dsanchez@...", "gsanchez@..."],
        subject="Resumen del día",
        html_body="<p>Hola Daniel...</p>",
    )

Token cacheado en memoria por ~50 min (Azure devuelve 1h TTL, dejamos buffer).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TIMEOUT = 60  # Phase V (2026-06-11): subido de 30s a 60s — Graph a veces tarda
RETRY_ATTEMPTS = 4  # cantidad de intentos para token y sendMail
RETRY_BACKOFF_BASE = 2.0  # 2s, 4s, 8s, 16s

_logger = logging.getLogger(__name__)

# Cache de token en memoria del proceso
_token_cache: dict[str, Any] = {"token": None, "expires_at": 0}


def _is_retriable_status(status: int) -> bool:
    """5xx + 429 (rate limit) + 408 (timeout) = reintentar.
    4xx (excepto 408/429) = NO reintentar — es error nuestro."""
    return status == 408 or status == 429 or status >= 500


def _get_token(force_refresh: bool = False) -> str:
    """Obtiene un access token usando client_credentials. Cachea ~50 min.
    Reintenta hasta 4 veces en caso de error 5xx/timeout."""
    now = time.time()
    if not force_refresh and _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    app_id = os.environ.get("MICROSOFT_APP_ID", "").strip()
    app_pwd = os.environ.get("MICROSOFT_APP_PASSWORD", "").strip()
    tenant = os.environ.get("MICROSOFT_APP_TENANT_ID", "").strip()
    if not (app_id and app_pwd and tenant):
        raise RuntimeError(
            "Faltan env vars: MICROSOFT_APP_ID, MICROSOFT_APP_PASSWORD, "
            "MICROSOFT_APP_TENANT_ID"
        )

    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": app_id,
        "client_secret": app_pwd,
        "scope": "https://graph.microsoft.com/.default",
    }

    last_err: str = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.post(url, data=data)
            if r.status_code >= 400:
                last_err = f"{r.status_code}: {r.text[:300]}"
                if _is_retriable_status(r.status_code) and attempt < RETRY_ATTEMPTS:
                    wait = RETRY_BACKOFF_BASE ** attempt
                    _logger.warning(
                        "OAuth token fail (attempt %d/%d) %s — reintentando en %ds",
                        attempt, RETRY_ATTEMPTS, last_err, wait,
                    )
                    time.sleep(wait)
                    continue
                raise RuntimeError(f"OAuth token fail {last_err}")
            body = r.json()
            break
        except httpx.RequestError as e:
            last_err = f"network: {e}"
            if attempt < RETRY_ATTEMPTS:
                wait = RETRY_BACKOFF_BASE ** attempt
                _logger.warning(
                    "OAuth token network err (attempt %d/%d) %s — reintentando en %ds",
                    attempt, RETRY_ATTEMPTS, last_err, wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"OAuth token network err: {e}") from e

    _token_cache["token"] = body["access_token"]
    # Azure devuelve 3600s, restamos 600s de buffer
    _token_cache["expires_at"] = now + max(0, int(body.get("expires_in", 3600)) - 600)
    return _token_cache["token"]


def _send_with_retry(url: str, payload: dict, attempt_token_refresh: bool = True) -> None:
    """Hace POST a Graph sendMail con retries automáticos.

    - Si el token expira mid-flight (401), refresca y reintenta.
    - 5xx/429/408 → backoff exponencial hasta RETRY_ATTEMPTS veces.
    - 4xx (no retriable) → falla inmediato con el error.
    """
    token = _get_token()
    last_err: str = ""
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                r = client.post(url, json=payload, headers=headers)
            if r.status_code < 400:
                return  # OK
            last_err = f"{r.status_code}: {r.text[:300]}"
            # Token expirado → refresh y reintenta
            if r.status_code == 401 and attempt_token_refresh:
                _logger.warning("Graph 401, refresh token y reintenta")
                token = _get_token(force_refresh=True)
                attempt_token_refresh = False
                continue
            if _is_retriable_status(r.status_code) and attempt < RETRY_ATTEMPTS:
                wait = RETRY_BACKOFF_BASE ** attempt
                _logger.warning(
                    "Graph sendMail retriable err (attempt %d/%d) %s — reintenta en %ds",
                    attempt, RETRY_ATTEMPTS, last_err, wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"Graph sendMail fail {last_err}")
        except httpx.RequestError as e:
            last_err = f"network: {e}"
            if attempt < RETRY_ATTEMPTS:
                wait = RETRY_BACKOFF_BASE ** attempt
                _logger.warning(
                    "Graph sendMail network err (attempt %d/%d) %s — reintenta en %ds",
                    attempt, RETRY_ATTEMPTS, last_err, wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"Graph sendMail network err: {e}") from e
    # Si salió del loop sin return ni raise — no debería pasar pero por las dudas
    raise RuntimeError(f"Graph sendMail dio up tras {RETRY_ATTEMPTS} intentos: {last_err}")


def send(
    from_user: str,
    to: list[str] | str,
    subject: str,
    html_body: str,
    cc: list[str] | str | None = None,
    save_to_sent: bool = True,
) -> None:
    """Envía un correo HTML desde `from_user` (UPN o email) usando Mail.Send App.

    Args:
        from_user: UPN del usuario en cuyo nombre se envía (ej. malvarado@...).
                   La app debe tener Mail.Send Application permission concedido.
        to: lista de emails destinatarios (o un solo email como string).
        subject: asunto del correo.
        html_body: cuerpo HTML.
        cc: lista de emails en copia (o un solo email, o None).
        save_to_sent: si True, queda en la carpeta "Enviados" del from_user.

    Raises:
        RuntimeError si el token falla o Graph rechaza el envío.
    """
    if isinstance(to, str):
        to = [to]
    if isinstance(cc, str):
        cc = [cc]
    cc = cc or []

    url = f"{GRAPH_BASE}/users/{from_user}/sendMail"

    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [
            {"emailAddress": {"address": e}} for e in to
        ],
    }
    if cc:
        message["ccRecipients"] = [
            {"emailAddress": {"address": e}} for e in cc
        ]

    payload = {"message": message, "saveToSentItems": save_to_sent}
    _send_with_retry(url, payload)


def send_email(
    to: list[str] | str,
    subject: str,
    html_body: str,
    cc: list[str] | str | None = None,
    *,
    from_user: str = "malvarado@biodegradablesecuador.com",
    inline_images: list[dict[str, Any]] | None = None,
) -> None:
    """Phase U+ (2026-06-10): wrapper compatible con `daily_report.send_email`.

    Acepta el mismo formato que el send_email histórico de pbi_cloud.py:
    `send_email(to, subject, html, cc=..., inline_images=[{filename, bytes, content_id, content_type}])`

    Si pasás inline_images, los embed como CID inline attachments del Graph API.
    """
    if isinstance(to, str):
        to = [to]
    if isinstance(cc, str):
        cc = [cc]
    cc = cc or []

    url = f"{GRAPH_BASE}/users/{from_user}/sendMail"

    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [
            {"emailAddress": {"address": e}} for e in to
        ],
    }
    if cc:
        message["ccRecipients"] = [
            {"emailAddress": {"address": e}} for e in cc
        ]

    if inline_images:
        import base64 as _b64
        attachments = []
        for img in inline_images:
            # Aceptar tanto el formato histórico (pbi_cloud) con
            # `content_bytes` + `name`, como mi formato nuevo con `bytes`
            # + `filename`. Lo que esté presente gana.
            raw = img.get("bytes") or img.get("content_bytes")
            if not raw:
                continue
            name = img.get("filename") or img.get("name") or "image.png"
            data_b64 = _b64.b64encode(raw).decode("ascii")
            attachments.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": img.get("content_type", "image/png"),
                "contentBytes": data_b64,
                "contentId": img.get("content_id", name),
                "isInline": True,
            })
        message["attachments"] = attachments

    payload = {"message": message, "saveToSentItems": True}
    _send_with_retry(url, payload)


if __name__ == "__main__":
    # Smoke test — envía un correo de prueba a Mateo desde Mateo
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    me = "malvarado@biodegradablesecuador.com"
    print(f"Enviando correo de prueba desde {me} a {me}...")
    send(
        from_user=me,
        to=me,
        subject="[Test] graph_mail.py",
        html_body="<h2>Funciona!</h2><p>Este correo fue enviado vía Graph con "
                  "Service Principal (sin MSAL cache).</p>",
    )
    print("[OK] enviado")
