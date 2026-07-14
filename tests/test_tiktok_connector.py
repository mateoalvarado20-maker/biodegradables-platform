"""Tests M3.0b — conector TikTok: PKCE, OAuth, rotación de tokens, cifrado
at-rest y revocación. HTTP siempre inyectado (cero red, regla de la casa)."""

import base64
import hashlib
import time
from urllib.parse import parse_qs, urlparse

import pytest

import tiktok_connector as tk


@pytest.fixture(autouse=True)
def _entorno(monkeypatch, tmp_path):
    monkeypatch.setattr(tk, "STATE_PATH", tmp_path / "tiktok_tokens.json")
    monkeypatch.setenv("TIKTOK_TOKEN_KEY",
                       base64.b64encode(b"k" * 32).decode("ascii"))
    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "ck-test")
    monkeypatch.setenv("TIKTOK_CLIENT_SECRET", "cs-test")
    monkeypatch.setenv("TIKTOK_REDIRECT_URI", "https://bot.test/oauth/tiktok/callback")
    monkeypatch.delenv("TIKTOK_TABLE_CONN", raising=False)
    monkeypatch.delenv("AzureWebJobsStorage", raising=False)


def _token_payload(n=1, now=None):
    return {
        "open_id": "open-123",
        "access_token": f"act-{n}",
        "refresh_token": f"rft-{n}",
        "expires_in": 86400,
        "refresh_expires_in": 31536000,
        "scope": "user.info.basic,video.publish",
    }


# ---------- PKCE + URL de autorización ----------

def test_pkce_challenge_es_s256_del_verifier():
    verifier, challenge = tk.gen_pkce()
    esperado = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == esperado
    assert 43 <= len(verifier) <= 128  # rango RFC 7636


def test_authorize_url_completa():
    url = tk.build_authorize_url(client_key="ck", redirect_uri="https://r/cb",
                                 state="t.abc", code_challenge="ch")
    q = parse_qs(urlparse(url).query)
    assert q["client_key"] == ["ck"]
    assert q["scope"] == ["user.info.basic,video.publish"]  # mínimos, sin extras
    assert q["response_type"] == ["code"]
    assert q["state"] == ["t.abc"]
    assert q["code_challenge_method"] == ["S256"]


# ---------- flujo conectar cuenta (start → callback) ----------

def test_flujo_completo_start_y_finish(monkeypatch):
    capturado = {}

    def http(method, url, *, data=None, json_body=None, headers=None):
        capturado.update(url=url, data=data)
        return _token_payload()

    out = tk.start_connect("biodegradables")
    q = parse_qs(urlparse(out["authorize_url"]).query)
    state = out["state"]
    assert state.startswith("biodegradables.")  # el tenant viaja en el state

    client = tk.TikTokClient(http=http)
    res = tk.finish_connect(state, "code-abc", client=client)
    assert res == {"tenant_id": "biodegradables", "open_id": "open-123",
                   "scopes": "user.info.basic,video.publish"}
    # el verifier canjeado corresponde al challenge de la URL (PKCE de verdad)
    verifier_enviado = capturado["data"]["code_verifier"]
    challenge_esperado = base64.urlsafe_b64encode(
        hashlib.sha256(verifier_enviado.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == [challenge_esperado]
    # y los tokens quedaron persistidos por tenant
    assert tk.connection_status("biodegradables")["connected"] is True
    assert tk.connection_status("otro-tenant")["connected"] is False  # aislamiento


def test_state_es_de_un_solo_uso():
    out = tk.start_connect("biodegradables")
    client = tk.TikTokClient(http=lambda *a, **k: _token_payload())
    tk.finish_connect(out["state"], "code", client=client)
    with pytest.raises(tk.TikTokError, match="state desconocido"):
        tk.finish_connect(out["state"], "code", client=client)


def test_state_expira_a_los_15_min():
    tk.save_pending_auth("t", "t.viejo", "verif", now=time.time() - 16 * 60)
    with pytest.raises(tk.TikTokError, match="15 min"):
        tk.pop_pending_auth("t.viejo", tenant_id="t")


def test_sin_app_configurada_error_claro(monkeypatch):
    monkeypatch.delenv("TIKTOK_CLIENT_KEY")
    with pytest.raises(tk.TikTokError, match="M3.0d"):
        tk.start_connect("biodegradables")


# ---------- refresh + rotación ----------

def _conectar(client):
    out = tk.start_connect("biodegradables")
    tk.finish_connect(out["state"], "code", client=client)


def test_refresh_rota_el_refresh_token_y_persiste(monkeypatch):
    llamadas = {"n": 0}

    def http(method, url, *, data=None, json_body=None, headers=None):
        llamadas["n"] += 1
        return _token_payload(n=llamadas["n"])

    reloj = {"t": 1_000_000.0}
    client = tk.TikTokClient(http=http, clock=lambda: reloj["t"])
    _conectar(client)

    reloj["t"] += 86400  # el access de 24 h ya venció
    token = tk.get_valid_access_token("biodegradables", client=client)
    assert token == "act-2"  # renovado
    assert tk.load_tokens("biodegradables").refresh_token == "rft-2"  # ROTADO y guardado

    # con access vigente NO renueva otra vez
    tk.get_valid_access_token("biodegradables", client=client)
    assert llamadas["n"] == 2  # exchange + un solo refresh


def test_refresh_token_vencido_pide_reconectar():
    reloj = {"t": 1_000_000.0}
    client = tk.TikTokClient(http=lambda *a, **k: _token_payload(),
                             clock=lambda: reloj["t"])
    _conectar(client)
    reloj["t"] += 32_000_000  # > 365 días
    with pytest.raises(tk.NotConnected, match="volver a conectar"):
        tk.get_valid_access_token("biodegradables", client=client)


def test_revocacion_se_detecta(monkeypatch):
    respuestas = [dict(_token_payload()), {"error": "invalid_grant"}]

    def http(method, url, **kwargs):
        return respuestas.pop(0)

    reloj = {"t": 1_000_000.0}
    client = tk.TikTokClient(http=http, clock=lambda: reloj["t"])
    _conectar(client)
    reloj["t"] += 86400
    with pytest.raises(tk.NotConnected, match="revocó"):
        tk.get_valid_access_token("biodegradables", client=client)


def test_error_oauth_en_exchange_es_claro():
    client = tk.TikTokClient(http=lambda *a, **k: {
        "error": "invalid_request", "error_description": "code inválido"})
    out = tk.start_connect("biodegradables")
    with pytest.raises(tk.TikTokError, match="invalid_request"):
        tk.finish_connect(out["state"], "code-malo", client=client)


# ---------- cifrado at-rest ----------

def test_tokens_no_quedan_en_claro_en_disco():
    client = tk.TikTokClient(http=lambda *a, **k: _token_payload())
    _conectar(client)
    crudo = tk.STATE_PATH.read_text(encoding="utf-8")
    assert "act-1" not in crudo and "rft-1" not in crudo
    assert tk.load_tokens("biodegradables").access_token == "act-1"  # roundtrip


def test_sin_llave_de_cifrado_fail_closed(monkeypatch):
    monkeypatch.delenv("TIKTOK_TOKEN_KEY")
    with pytest.raises(tk.TikTokError, match="TIKTOK_TOKEN_KEY"):
        tk.save_tokens("t", tk.TokenSet(
            open_id="o", access_token="a", refresh_token="r",
            expires_at=1.0, refresh_expires_at=2.0))


def test_llave_invalida_rechazada(monkeypatch):
    monkeypatch.setenv("TIKTOK_TOKEN_KEY",
                       base64.b64encode(b"corta").decode("ascii"))
    with pytest.raises(tk.TikTokError, match="32 bytes"):
        tk.seal({"x": 1})


def test_sin_cuenta_conectada_not_connected():
    with pytest.raises(tk.NotConnected, match="sin cuenta TikTok"):
        tk.get_valid_access_token("tenant-sin-conectar",
                                  client=tk.TikTokClient(http=lambda *a, **k: {}))
