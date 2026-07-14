"""Conector TikTok — M3.0b (OAuth2 + tokens + cliente HTTP; SIN publicar).

Módulo compartido bot ↔ PC (vive en la raíz: viaja en el zip del bot).
Arquitectura aprobada por el board 2026-07-14:

- UNA app de VER-IA (client_key/client_secret en app settings → Key Vault en
  SaaS); N cuentas de clientes conectadas a esa app.
- Multi-tenant desde el inicio: TODO se guarda por tenant_id (PartitionKey).
- El flujo OAuth vive en el BOT (única superficie HTTPS pública):
  connect-start genera state+PKCE → el cliente autoriza en TikTok → el
  callback canjea el code → tokens CIFRADOS (AES-GCM, llave en
  TIKTOK_TOKEN_KEY) en Azure Table. La PC jamás ve refresh_token ni llave:
  pide un access_token vigente por el admin API cuando publique (M3.1).
- Tokens TikTok: access ~24 h; refresh ~365 días y ROTATIVO — cada refresh
  puede devolver uno nuevo que reemplaza al anterior (se persiste siempre).

Este módulo NO publica: expone el cliente HTTP (`creator_info`, `video_init`,
`status_fetch`) que el TikTokPublisher de M3.1 usará detrás del puerto
Publisher y de sus 3 capas de kill-switch. `http` inyectable en todo (cero
red en pytest).

Env vars (bot; la PC no necesita ninguna):
  TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET  — de la app de VER-IA (M3.0d 👤)
  TIKTOK_REDIRECT_URI                        — la registrada en la app
  TIKTOK_TOKEN_KEY                           — 32 bytes base64 (cifrado at-rest)
  TIKTOK_TABLE_CONN | AzureWebJobsStorage    — storage de tokens
"""
from __future__ import annotations

import base64
import functools
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import safe_json

logger = logging.getLogger("tiktok_connector")

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
CREATOR_INFO_URL = "https://open.tiktokapis.com/v2/post/publish/creator_info/query/"
VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_FETCH_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# Mínimos y nada más (board 2026-07-14). video.list recién en M3.2.
SCOPES = ("user.info.basic", "video.publish")

TABLE_NAME = "tiktoktokens"
PENDING_TTL_S = 15 * 60  # una autorización pendiente vale 15 min
REFRESH_MARGIN_S = 30 * 60  # renovar 30 min antes de expirar (doc oficial: 10-30)

STATE_PATH = Path(
    os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")
) / "tiktok_tokens.json"
_LOCK = safe_json.lock_for(STATE_PATH)


class TikTokError(RuntimeError):
    pass


class NotConnected(TikTokError):
    """El tenant no tiene cuenta TikTok conectada (o revocó el acceso)."""


# --------------- PKCE ---------------
def gen_pkce() -> tuple[str, str]:
    """(verifier, challenge S256). OJO M3.1: validar contra la app real —
    la doc de TikTok ha variado entre base64url y hex para el challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_authorize_url(*, client_key: str, redirect_uri: str, state: str,
                        code_challenge: str, scopes: tuple[str, ...] = SCOPES) -> str:
    return AUTHORIZE_URL + "?" + urlencode({
        "client_key": client_key,
        "scope": ",".join(scopes),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })


# --------------- Cifrado de tokens (AES-GCM) ---------------
def _key() -> bytes:
    raw = os.environ.get("TIKTOK_TOKEN_KEY", "").strip()
    if not raw:
        raise TikTokError(
            "falta TIKTOK_TOKEN_KEY (32 bytes base64) — los tokens NO se "
            "guardan en claro, fail-closed por diseño"
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise TikTokError("TIKTOK_TOKEN_KEY debe ser exactamente 32 bytes base64")
    return key


def seal(data: dict) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = secrets.token_bytes(12)
    ct = AESGCM(_key()).encrypt(nonce, json.dumps(data).encode("utf-8"), b"tiktok-v1")
    return base64.b64encode(nonce + ct).decode("ascii")


def unseal(blob: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(blob)
    pt = AESGCM(_key()).decrypt(raw[:12], raw[12:], b"tiktok-v1")
    return json.loads(pt.decode("utf-8"))


# --------------- Tokens ---------------
@dataclass
class TokenSet:
    open_id: str
    access_token: str
    refresh_token: str
    expires_at: float  # epoch s
    refresh_expires_at: float
    scopes: str = ""

    def access_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at - REFRESH_MARGIN_S

    def refresh_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.refresh_expires_at


def _tokenset_from_response(payload: dict, now: float) -> TokenSet:
    if payload.get("error"):
        raise TikTokError(f"TikTok OAuth error: {payload.get('error')} — "
                          f"{payload.get('error_description', '')}"[:300])
    try:
        return TokenSet(
            open_id=payload["open_id"],
            access_token=payload["access_token"],
            refresh_token=payload["refresh_token"],
            expires_at=now + float(payload["expires_in"]),
            refresh_expires_at=now + float(payload["refresh_expires_in"]),
            scopes=payload.get("scope", ""),
        )
    except KeyError as exc:
        raise TikTokError(f"respuesta de token incompleta: falta {exc}") from exc


# --------------- HTTP (inyectable) ---------------
# http(method, url, *, data=None, json_body=None, headers=None) -> dict
Http = Callable[..., dict]


def _default_http(method: str, url: str, *, data: dict | None = None,
                  json_body: dict | None = None, headers: dict | None = None) -> dict:
    import httpx

    resp = httpx.request(method, url, data=data, json=json_body,
                         headers=headers, timeout=30.0)
    # TikTok devuelve errores con cuerpo JSON útil — no levantar sin leerlo
    try:
        payload = resp.json()
    except Exception:
        resp.raise_for_status()
        raise TikTokError(f"respuesta no-JSON de {url} ({resp.status_code})")
    if resp.status_code >= 500:
        raise TikTokError(f"TikTok {resp.status_code} en {url}: {str(payload)[:200]}")
    return payload


class TikTokClient:
    """Cliente HTTP mínimo de la Content Posting API. Sin estado propio."""

    def __init__(self, *, client_key: str = "", client_secret: str = "",
                 http: Http | None = None, clock: Callable[[], float] = time.time):
        self.client_key = client_key or os.environ.get("TIKTOK_CLIENT_KEY", "")
        self.client_secret = client_secret or os.environ.get("TIKTOK_CLIENT_SECRET", "")
        self._http = http or _default_http
        self._clock = clock

    def now(self) -> float:
        return self._clock()

    def _require_app(self) -> None:
        if not self.client_key or not self.client_secret:
            raise TikTokError(
                "app de TikTok no configurada (TIKTOK_CLIENT_KEY/SECRET) — "
                "pendiente M3.0d (registro de la app de VER-IA)"
            )

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> TokenSet:
        self._require_app()
        payload = self._http("POST", TOKEN_URL, data={
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        return _tokenset_from_response(payload, self._clock())

    def refresh(self, tokens: TokenSet) -> TokenSet:
        """Renueva el access token. ROTACIÓN: si TikTok devuelve refresh_token
        nuevo, reemplaza al viejo — persistir SIEMPRE el TokenSet devuelto."""
        self._require_app()
        if tokens.refresh_expired(self._clock()):
            raise NotConnected(
                "refresh token vencido (>365 días) — el cliente debe volver a "
                "conectar su cuenta (flujo de autorización)"
            )
        payload = self._http("POST", TOKEN_URL, data={
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh_token,
        }, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if payload.get("error") in ("invalid_grant", "access_denied"):
            raise NotConnected(f"TikTok revocó el acceso: {payload.get('error')}")
        return _tokenset_from_response(payload, self._clock())

    # ---- Content Posting API (los usa el publisher de M3.1, NO este módulo) ----
    def creator_info(self, access_token: str) -> dict:
        return self._http("POST", CREATOR_INFO_URL, json_body={},
                          headers={"Authorization": f"Bearer {access_token}"})

    def video_init_pull(self, access_token: str, *, title: str, video_url: str,
                        privacy_level: str = "SELF_ONLY") -> dict:
        """Direct Post con PULL_FROM_URL (exige dominio verificado del que
        TikTok pueda bajar el MP4). Pre-auditoría TikTok FUERZA SELF_ONLY —
        pasarlo explícito documenta la intención."""
        return self._http("POST", VIDEO_INIT_URL, json_body={
            "post_info": {"title": title, "privacy_level": privacy_level},
            "source_info": {"source": "PULL_FROM_URL", "video_url": video_url},
        }, headers={"Authorization": f"Bearer {access_token}"})

    def video_init_upload(self, access_token: str, *, title: str, video_size: int,
                          chunk_size: int, total_chunk_count: int,
                          privacy_level: str = "SELF_ONLY") -> dict:
        """Direct Post con FILE_UPLOAD — la vía que usará M3.1 (los MP4 viven
        en la PC, sin URL pública). Devuelve upload_url + publish_id; la subida
        por chunks la implementa el publisher de M3.1."""
        return self._http("POST", VIDEO_INIT_URL, json_body={
            "post_info": {"title": title, "privacy_level": privacy_level},
            "source_info": {"source": "FILE_UPLOAD", "video_size": video_size,
                            "chunk_size": chunk_size,
                            "total_chunk_count": total_chunk_count},
        }, headers={"Authorization": f"Bearer {access_token}"})

    def status_fetch(self, access_token: str, publish_id: str) -> dict:
        return self._http("POST", STATUS_FETCH_URL,
                          json_body={"publish_id": publish_id},
                          headers={"Authorization": f"Bearer {access_token}"})


# --------------- Store multi-tenant (Azure Table | archivo local) ---------------
def _conn_str() -> str:
    return (os.environ.get("TIKTOK_TABLE_CONN", "").strip()
            or os.environ.get("AzureWebJobsStorage", "").strip())


def _is_table() -> bool:
    return bool(_conn_str())


def _table_client():
    from azure.data.tables import TableServiceClient
    service = TableServiceClient.from_connection_string(_conn_str())
    try:
        service.create_table_if_not_exists(TABLE_NAME)
    except Exception:
        pass
    return service.get_table_client(TABLE_NAME)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _row_get(tenant_id: str, row_key: str) -> dict | None:
    if _is_table():
        try:
            ent = _table_client().get_entity(partition_key=tenant_id, row_key=row_key)
            return dict(ent)
        except Exception:
            return None
    state = safe_json.load_json(STATE_PATH, dict)
    return state.get(tenant_id, {}).get(row_key)


def _row_put(tenant_id: str, row_key: str, fields: dict) -> None:
    if _is_table():
        _table_client().upsert_entity(
            {"PartitionKey": tenant_id, "RowKey": row_key, **fields})
        return
    state = safe_json.load_json(STATE_PATH, dict)
    state.setdefault(tenant_id, {})[row_key] = fields
    safe_json.save_json(STATE_PATH, state, sort_keys=True)


def _row_delete(tenant_id: str, row_key: str) -> None:
    if _is_table():
        try:
            _table_client().delete_entity(partition_key=tenant_id, row_key=row_key)
        except Exception:
            pass
        return
    state = safe_json.load_json(STATE_PATH, dict)
    state.get(tenant_id, {}).pop(row_key, None)
    safe_json.save_json(STATE_PATH, state, sort_keys=True)


@_locked
def save_pending_auth(tenant_id: str, state: str, verifier: str,
                      now: float | None = None) -> None:
    _row_put(tenant_id, f"pending:{state}",
             {"blob": seal({"verifier": verifier}),
              "created_at": now if now is not None else time.time()})


@_locked
def pop_pending_auth(state_value: str, *, tenant_id: str,
                     now: float | None = None) -> str:
    """Devuelve el verifier del state y lo consume (un solo uso, anti-CSRF)."""
    row = _row_get(tenant_id, f"pending:{state_value}")
    if row is None:
        raise TikTokError("state desconocido o ya usado — reiniciar la conexión")
    _row_delete(tenant_id, f"pending:{state_value}")
    if ((now if now is not None else time.time()) - float(row["created_at"])) > PENDING_TTL_S:
        raise TikTokError("la autorización tardó más de 15 min — reiniciar la conexión")
    return unseal(row["blob"])["verifier"]


@_locked
def save_tokens(tenant_id: str, tokens: TokenSet) -> None:
    _row_put(tenant_id, "account", {
        "blob": seal(asdict(tokens)),
        "open_id": tokens.open_id,
        "scopes": tokens.scopes,
        "expires_at": tokens.expires_at,
        "refresh_expires_at": tokens.refresh_expires_at,
    })


def load_tokens(tenant_id: str) -> TokenSet:
    row = _row_get(tenant_id, "account")
    if row is None:
        raise NotConnected(f"tenant {tenant_id}: sin cuenta TikTok conectada")
    return TokenSet(**unseal(row["blob"]))


def connection_status(tenant_id: str) -> dict:
    """Estado SIN secretos (para el endpoint de status y el dashboard M2)."""
    row = _row_get(tenant_id, "account")
    if row is None:
        return {"connected": False}
    return {
        "connected": True,
        "open_id": row.get("open_id", ""),
        "scopes": row.get("scopes", ""),
        "access_expira_en_s": max(0, int(float(row.get("expires_at", 0)) - time.time())),
        "refresh_expira_en_s": max(0, int(float(row.get("refresh_expires_at", 0)) - time.time())),
    }


def get_valid_access_token(tenant_id: str, client: TikTokClient | None = None) -> str:
    """Access token vigente, renovando (con rotación persistida) si hace
    falta. Es lo ÚNICO que la PC recibe del bot en M3.1."""
    client = client or TikTokClient()
    tokens = load_tokens(tenant_id)
    if tokens.access_expired(client.now()):
        tokens = client.refresh(tokens)
        save_tokens(tenant_id, tokens)  # rotación: persistir SIEMPRE el nuevo set
        logger.info("tiktok: access token renovado para %s", tenant_id)
    return tokens.access_token


def start_connect(tenant_id: str, *, redirect_uri: str = "",
                  client_key: str = "") -> dict:
    """Paso 1 del onboarding: genera la URL de autorización y deja el
    verifier PKCE guardado (cifrado) esperando el callback."""
    client_key = client_key or os.environ.get("TIKTOK_CLIENT_KEY", "")
    redirect_uri = redirect_uri or os.environ.get("TIKTOK_REDIRECT_URI", "")
    if not client_key or not redirect_uri:
        raise TikTokError(
            "app de TikTok no configurada (TIKTOK_CLIENT_KEY + "
            "TIKTOK_REDIRECT_URI) — pendiente M3.0d"
        )
    verifier, challenge = gen_pkce()
    state = f"{tenant_id}.{secrets.token_urlsafe(24)}"
    save_pending_auth(tenant_id, state, verifier)
    return {
        "authorize_url": build_authorize_url(
            client_key=client_key, redirect_uri=redirect_uri,
            state=state, code_challenge=challenge),
        "state": state,
    }


def finish_connect(state: str, code: str, *, redirect_uri: str = "",
                   client: TikTokClient | None = None) -> dict:
    """Paso 2 (callback): valida state, canjea el code, guarda tokens.
    El tenant viaja DENTRO del state (formato `<tenant>.<nonce>`)."""
    tenant_id = state.split(".", 1)[0] if "." in state else ""
    if not tenant_id:
        raise TikTokError("state sin tenant — reiniciar la conexión")
    verifier = pop_pending_auth(state, tenant_id=tenant_id)
    redirect_uri = redirect_uri or os.environ.get("TIKTOK_REDIRECT_URI", "")
    client = client or TikTokClient()
    tokens = client.exchange_code(code, verifier, redirect_uri)
    save_tokens(tenant_id, tokens)
    logger.info("tiktok: cuenta conectada para tenant %s (open_id=%s)",
                tenant_id, tokens.open_id)
    return {"tenant_id": tenant_id, "open_id": tokens.open_id,
            "scopes": tokens.scopes}
