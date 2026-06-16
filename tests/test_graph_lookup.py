"""Tests de graph_mail.lookup_user_email (resolución de identidad por Graph).

La solución correcta a la caída de identidad (Teams dejó de mandar el email en
props): resolver por AAD object id vía Graph. No hace red — mockea httpx + token.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import graph_mail


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    last = None

    def __init__(self, resp, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, headers=None, params=None):
        _FakeClient.last = {"url": url, "headers": headers, "params": params}
        if self._raise:
            raise self._raise
        return self._resp


def _patch(monkeypatch, resp=None, raise_exc=None):
    monkeypatch.setattr(graph_mail, "_get_token", lambda *a, **k: "FAKE")
    monkeypatch.setattr(
        graph_mail.httpx, "Client",
        lambda *a, **k: _FakeClient(resp, raise_exc),
    )


def test_resuelve_por_mail(monkeypatch):
    _patch(monkeypatch, _FakeResp(200, {"mail": "Dsanchez@Biodegradablesecuador.com"}))
    email = graph_mail.lookup_user_email("fdd1f7f1-aaaa-bbbb-cccc-dddddddddddd")
    assert email == "dsanchez@biodegradablesecuador.com"  # lowercased
    # usó token app-only y el path correcto
    assert _FakeClient.last["headers"]["Authorization"] == "Bearer FAKE"
    assert _FakeClient.last["url"].endswith("/users/fdd1f7f1-aaaa-bbbb-cccc-dddddddddddd")


def test_cae_a_userprincipalname(monkeypatch):
    _patch(monkeypatch, _FakeResp(200, {"mail": None, "userPrincipalName": "x@y.com"}))
    assert graph_mail.lookup_user_email("id-1") == "x@y.com"


def test_403_sin_permiso_devuelve_vacio(monkeypatch):
    _patch(monkeypatch, _FakeResp(403, text="Authorization_RequestDenied"))
    assert graph_mail.lookup_user_email("id-1") == ""


def test_404_devuelve_vacio(monkeypatch):
    _patch(monkeypatch, _FakeResp(404))
    assert graph_mail.lookup_user_email("id-1") == ""


def test_error_de_red_devuelve_vacio(monkeypatch):
    _patch(monkeypatch, raise_exc=graph_mail.httpx.RequestError("boom"))
    assert graph_mail.lookup_user_email("id-1") == ""


def test_id_vacio_no_pega_a_graph(monkeypatch):
    called = {"token": False}
    monkeypatch.setattr(graph_mail, "_get_token",
                        lambda *a, **k: called.__setitem__("token", True) or "X")
    assert graph_mail.lookup_user_email("") == ""
    assert called["token"] is False  # ni siquiera pide token
