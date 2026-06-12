"""Cliente Power BI cloud + envío de correo (Microsoft Graph).

Autenticación: MSAL device-code flow la primera vez, refresh silencioso después.
El token cache se guarda en ~/.claude-agent/msal_cache.bin para que Task Scheduler
pueda correr sin intervención una vez que te autenticas una sola vez.

Permisos requeridos en el app registration de Azure AD (claude-agent):
- Microsoft Graph:    Mail.Send (Delegated)        [YA CONFIGURADO]
- Microsoft Graph:    User.Read (Delegated)        [YA CONFIGURADO]
- Power BI Service:   Dataset.Read.All (Delegated) [FALTA AGREGAR]
"""
from __future__ import annotations

import atexit
import base64
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import msal

CLIENT_ID = os.environ.get("GRAPH_CLIENT_ID", "")
TENANT_ID = os.environ.get("GRAPH_TENANT_ID", "common")
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# Scopes
PBI_SCOPES = ["https://analysis.windows.net/powerbi/api/Dataset.Read.All"]
MAIL_SCOPES = ["https://graph.microsoft.com/Mail.Send"]

# Token cache persistente (sobrevive entre ejecuciones).
# Soporta dos modos:
#  1) Local (PC Mateo): archivo en ~/.claude-agent/msal_cache.bin
#  2) Azure Functions: env var MSAL_CACHE_B64 con el cache base64-encoded
#     (Mateo lo refresca cada ~90 días corriendo `pbi_cloud.py` interactivo).
CACHE_PATH = Path.home() / ".claude-agent" / "msal_cache.bin"
CACHE_ENV_VAR = "MSAL_CACHE_B64"

_token_cache: msal.SerializableTokenCache | None = None
_app: msal.PublicClientApplication | None = None


def _get_cache() -> msal.SerializableTokenCache:
    global _token_cache
    if _token_cache is not None:
        return _token_cache
    _token_cache = msal.SerializableTokenCache()

    # Modo Azure Functions: cache vía env var (base64 del JSON serializado).
    # Tiene prioridad sobre el archivo local (que en Azure no existe).
    b64 = os.environ.get(CACHE_ENV_VAR, "").strip()
    if b64:
        try:
            decoded = base64.b64decode(b64).decode("utf-8")
            _token_cache.deserialize(decoded)
        except Exception as e:
            print(f"[WARN] no pude decodificar {CACHE_ENV_VAR}: {e}",
                  file=sys.stderr)
    elif CACHE_PATH.exists():
        # Modo local: archivo persistente
        try:
            _token_cache.deserialize(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    atexit.register(_save_cache)
    return _token_cache


def _save_cache() -> None:
    if _token_cache is None or not _token_cache.has_state_changed:
        return
    # En Azure Functions el filesystem no es persistente entre invocaciones,
    # así que no intentamos escribir; el access_token recién obtenido vive
    # solo durante esta corrida. El refresh_token sigue siendo el del env var.
    if os.environ.get(CACHE_ENV_VAR):
        return
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(_token_cache.serialize(), encoding="utf-8")


def _get_app() -> msal.PublicClientApplication:
    global _app
    if _app is not None:
        return _app
    if not CLIENT_ID:
        raise RuntimeError("Falta GRAPH_CLIENT_ID en variables de entorno.")
    _app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=_get_cache(),
    )
    return _app


def get_token(scopes: list[str], *, interactive_ok: bool = True) -> str:
    """Devuelve un access token para los scopes pedidos.

    Usa refresh silencioso si hay cuenta cacheada. Si no, hace device flow.
    """
    app = _get_app()
    result: dict[str, Any] | None = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes, account=accounts[0])
    if result and "access_token" in result:
        return result["access_token"]

    if not interactive_ok:
        raise RuntimeError(
            f"No hay token válido en cache para {scopes}. "
            "Corre interactivamente primero para autenticar."
        )

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow falló: {flow}")
    print(f"\n[AUTH] {flow['message']}\n", flush=True)
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Auth falló: {result.get('error_description')}")
    _save_cache()
    return result["access_token"]


# ===== Power BI REST API =====
PBI_BASE = "https://api.powerbi.com/v1.0/myorg"


def _pbi_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    interactive_ok: bool = True,
) -> dict:
    token = get_token(PBI_SCOPES, interactive_ok=interactive_ok)
    r = httpx.request(
        method,
        f"{PBI_BASE}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json_body,
        timeout=60,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"PBI {method} {path} → {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def list_workspaces() -> list[dict]:
    """Lista los workspaces compartidos (requiere Workspace.Read.All).

    Si el scope no está concedido, devuelve [] silenciosamente — no es crítico
    porque 'Mi área de trabajo' se accede sin este permiso.
    """
    try:
        return _pbi_request("GET", "/groups").get("value", [])
    except RuntimeError as e:
        if "401" in str(e) or "403" in str(e):
            return []
        raise


def list_datasets(workspace_id: str | None = None) -> list[dict]:
    """Lista datasets. workspace_id=None → 'Mi área de trabajo'."""
    path = "/datasets" if workspace_id is None else f"/groups/{workspace_id}/datasets"
    return _pbi_request("GET", path).get("value", [])


def get_last_refresh(dataset_id: str) -> str | None:
    """Devuelve el endTime ISO del último refresh exitoso, o None."""
    try:
        data = _pbi_request("GET", f"/datasets/{dataset_id}/refreshes?$top=5")
        for r in data.get("value", []):
            if r.get("status") == "Completed":
                return r.get("endTime") or r.get("startTime")
    except Exception:
        pass
    return None


def execute_dax(
    dataset_id: str,
    dax: str,
    workspace_id: str | None = None,
    *,
    interactive_ok: bool = True,
) -> dict:
    """Ejecuta un query DAX. Devuelve el JSON crudo de la respuesta."""
    if workspace_id:
        path = f"/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
    else:
        path = f"/datasets/{dataset_id}/executeQueries"
    return _pbi_request(
        "POST",
        path,
        json_body={
            "queries": [{"query": dax}],
            "serializerSettings": {"includeNulls": True},
        },
        interactive_ok=interactive_ok,
    )


def dax_rows(result: dict) -> list[dict]:
    """Saca las filas planas de la respuesta de executeQueries."""
    try:
        return result["results"][0]["tables"][0]["rows"]
    except (KeyError, IndexError):
        return []


# ===== Microsoft Graph — enviar correo =====
def send_email(
    to: str | list[str],
    subject: str,
    body_html: str,
    *,
    cc: str | list[str] | None = None,
    inline_images: list[dict] | None = None,
    interactive_ok: bool = True,
) -> None:
    """Envía un correo HTML opcionalmente con imágenes inline.

    inline_images: lista de dicts con:
      - name: nombre del archivo (ej. "chart.png")
      - content_bytes: bytes crudos de la imagen
      - content_id: ID a referenciar en HTML como <img src="cid:ID">
      - content_type: MIME type (default "image/png")
    """
    import base64

    token = get_token(MAIL_SCOPES, interactive_ok=interactive_ok)
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
        attachments = []
        for img in inline_images:
            attachments.append(
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": img["name"],
                    "contentType": img.get("content_type", "image/png"),
                    "contentBytes": base64.b64encode(img["content_bytes"]).decode(),
                    "contentId": img["content_id"],
                    "isInline": True,
                }
            )
        payload["message"]["attachments"] = attachments
    r = httpx.post(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 202):
        raise RuntimeError(f"sendMail falló {r.status_code}: {r.text[:500]}")


if __name__ == "__main__":
    # Smoke test: forzar auth si hace falta y mostrar info de cuenta
    print("Autenticando contra Power BI...")
    get_token(PBI_SCOPES)
    print("OK - token PBI obtenido.")
    print("\nAutenticando contra Microsoft Graph (Mail)...")
    get_token(MAIL_SCOPES)
    print("OK - token Mail obtenido.")
    print(f"\nToken cache en: {CACHE_PATH}")
