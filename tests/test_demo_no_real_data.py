"""Fase 0 — red de seguridad anti-fuga del entorno DEMO.

Verifica las dos garantías:
  1. graph_mail: en DEMO_MODE ningún correo puede salir a una dirección real
     (redirección + fail-closed), y SIN el flag el comportamiento no cambia.
  2. demo_guard: detección de identificadores del cliente real en contenido
     renderizado, y validación de arranque del entorno demo.

Ningún test hace red: `graph_mail._send_with_retry` se monkeypatchea para
capturar (url, payload) en memoria.
"""
from __future__ import annotations

import pytest

import demo_guard
import graph_mail


@pytest.fixture()
def captured(monkeypatch):
    """Captura lo que graph_mail intentaría enviar, sin tocar la red."""
    box: dict = {}

    def _fake_send(url, payload, attempt_token_refresh=True):
        box["url"] = url
        box["payload"] = payload

    monkeypatch.setattr(graph_mail, "_send_with_retry", _fake_send)
    return box


def _addresses(payload: dict) -> list[str]:
    msg = payload["message"]
    out = []
    for key in ("toRecipients", "ccRecipients", "bccRecipients"):
        for r in msg.get(key, []) or []:
            out.append(r["emailAddress"]["address"])
    return out


# ===== graph_mail: comportamiento SIN DEMO_MODE (producción intacta) =====

def test_passthrough_when_demo_off(captured, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    graph_mail.send(
        from_user="malvarado@biodegradablesecuador.com",
        to=["dsanchez@biodegradablesecuador.com"],
        subject="Reporte del día",
        html_body="<p>hola</p>",
        cc=["gsanchez@biodegradablesecuador.com"],
    )
    # Sin el flag: remitente, destinatarios y asunto intactos.
    assert "malvarado@biodegradablesecuador.com" in captured["url"]
    addrs = _addresses(captured["payload"])
    assert "dsanchez@biodegradablesecuador.com" in addrs
    assert "gsanchez@biodegradablesecuador.com" in addrs
    assert captured["payload"]["message"]["subject"] == "Reporte del día"


# ===== graph_mail: comportamiento CON DEMO_MODE =====

@pytest.fixture()
def demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_EMAIL_TO", "demo@andexdemo.com")
    monkeypatch.setenv("DEMO_FROM_USER", "amora@andexdemo.com")
    monkeypatch.setenv("DEMO_SUBJECT_PREFIX", "[DEMO] ")


def test_demo_redirects_every_recipient(captured, demo_env):
    graph_mail.send(
        from_user="malvarado@biodegradablesecuador.com",
        to=["dsanchez@biodegradablesecuador.com"],
        subject="Reporte del día",
        html_body="<p>hola</p>",
        cc=["gsanchez@biodegradablesecuador.com"],
    )
    # Remitente reescrito al buzón demo.
    assert "amora@andexdemo.com" in captured["url"]
    assert "biodegradablesecuador" not in captured["url"]
    # TODOS los destinatarios redirigidos a la bandeja demo; CC eliminado.
    addrs = _addresses(captured["payload"])
    assert addrs == ["demo@andexdemo.com"]
    assert not any("biodegradablesecuador" in a for a in addrs)
    # Asunto con prefijo.
    assert captured["payload"]["message"]["subject"] == "[DEMO] Reporte del día"


def test_demo_send_email_wrapper_also_redirects(captured, demo_env):
    graph_mail.send_email(
        to="dsanchez@biodegradablesecuador.com",
        subject="KPIs",
        html_body="<p>x</p>",
        from_user="malvarado@biodegradablesecuador.com",
    )
    assert "amora@andexdemo.com" in captured["url"]
    assert _addresses(captured["payload"]) == ["demo@andexdemo.com"]


def test_demo_subject_prefix_not_doubled(captured, demo_env):
    graph_mail.send(
        from_user="x@biodegradablesecuador.com",
        to="y@biodegradablesecuador.com",
        subject="[DEMO] Ya tiene prefijo",
        html_body="<p>x</p>",
    )
    assert captured["payload"]["message"]["subject"] == "[DEMO] Ya tiene prefijo"


def test_demo_fails_closed_on_real_inbox(captured, monkeypatch):
    """Si DEMO_EMAIL_TO apunta a un dominio real, el envío se ABORTA."""
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_EMAIL_TO", "dsanchez@biodegradablesecuador.com")
    with pytest.raises(RuntimeError, match="dominio demo"):
        graph_mail.send(
            from_user="amora@andexdemo.com",
            to="amora@andexdemo.com",
            subject="x",
            html_body="<p>x</p>",
        )
    assert "url" not in captured  # nunca llegó al envío


def test_payload_guard_blocks_handcrafted_real_recipient(monkeypatch):
    """El guard del chokepoint atrapa payloads que evitan _apply_demo_sandbox."""
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    payload = {
        "message": {
            "subject": "x",
            "toRecipients": [
                {"emailAddress": {"address": "dsanchez@biodegradablesecuador.com"}}
            ],
        }
    }
    with pytest.raises(RuntimeError, match="ABORTADO"):
        graph_mail._send_with_retry("https://graph/x/sendMail", payload)


# ===== demo_guard: scanner de contenido renderizado =====

def test_scan_detects_company_and_people():
    html = "<p>Estimado Daniel Sánchez, gerente de Biodegradables Ecuador</p>"
    hits = demo_guard.scan_for_real_data(html)
    assert "biodegradables ecuador" in hits
    assert "daniel sánchez" in hits


def test_scan_clean_demo_content_passes():
    html = "<p>Estimado Roberto Salinas, gerente de Andex</p>"
    assert demo_guard.scan_for_real_data(html) == []
    demo_guard.assert_no_real_data(html)  # no levanta


def test_assert_raises_on_real_data():
    with pytest.raises(RuntimeError, match="Fuga de datos reales"):
        demo_guard.assert_no_real_data(
            "correo a malvarado@biodegradablesecuador.com", context="reporte"
        )


def test_scan_extra_forbidden_via_env(monkeypatch):
    monkeypatch.setenv("DEMO_FORBIDDEN_EXTRA", "acmecorp,juan perez")
    assert "acmecorp" in demo_guard.scan_for_real_data("cliente AcmeCorp S.A.")


# ===== demo_guard: verificación de arranque =====

def test_verify_config_noop_when_demo_off(monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    demo_guard.verify_demo_config()  # no levanta aunque el slug sea el real


def test_verify_config_blocks_real_tenant(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_SLUG", "biodegradables")
    with pytest.raises(RuntimeError, match="cliente REAL"):
        demo_guard.verify_demo_config()


def test_verify_config_blocks_real_mail_domain(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_EMAIL_TO", "demo@biodegradablesecuador.com")
    with pytest.raises(RuntimeError, match="dominio demo"):
        demo_guard.verify_demo_config()


def test_verify_config_passes_for_valid_demo(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_EMAIL_TO", "demo@andexdemo.com")
    monkeypatch.setenv("DEMO_FROM_USER", "amora@andexdemo.com")
    demo_guard.verify_demo_config()  # no levanta
