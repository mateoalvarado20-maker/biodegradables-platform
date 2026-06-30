"""Tests de contifico_client.clientes_sin_credito_con_saldo.

Clientes con saldo pendiente que NO tienen crédito aprobado (no están en el
Excel de condiciones de crédito) — facturados sin registrar el pago. Es el
complemento exacto de cartera_vencida_por_ciudad.
"""
import datetime as dt

import contifico_client as cc


def _doc(documento, cliente, saldo, fecha):
    return {
        "documento": documento,
        "persona": {"razon_social": cliente},
        "saldo": saldo,
        "fecha_emision": fecha,  # DD/MM/YYYY
        "anulado": False,
    }


def _setup(monkeypatch, docs):
    monkeypatch.setattr(cc, "get_documentos", lambda fi, ff, **k: list(docs))
    # CON CREDITO SA tiene plazo; el resto sin crédito (None).
    monkeypatch.setattr(
        cc, "_get_plazo_cliente",
        lambda nombre: 30 if "CON CREDITO" in nombre else None,
    )


def test_solo_clientes_sin_credito_y_agrega_por_cliente(monkeypatch):
    today = dt.date(2026, 6, 30)
    docs = [
        _doc("001-002-000000001", "ACME SA", 500.0, "01/05/2026"),       # UIO sin crédito
        _doc("001-002-000000002", "ACME SA", 300.0, "10/05/2026"),       # UIO sin crédito (mismo cli)
        _doc("001-002-000000003", "CON CREDITO SA", 999.0, "01/05/2026"),  # con crédito → excluir
        _doc("001-002-000000004", "CENTAVOS SA", 0.5, "01/05/2026"),     # saldo trivial → excluir
        _doc("001-001-000000005", "GYE CLIENTE", 200.0, "01/05/2026"),   # GYE
    ]
    _setup(monkeypatch, docs)

    uio = cc.clientes_sin_credito_con_saldo("UIO", n=5, fecha_referencia=today)
    nombres = [r["cliente"] for r in uio]
    assert "ACME SA" in nombres
    assert "CON CREDITO SA" not in nombres   # tiene crédito → va a cartera_vencida
    assert "CENTAVOS SA" not in nombres      # saldo ≤ $1

    acme = next(r for r in uio if r["cliente"] == "ACME SA")
    assert acme["saldo_pendiente"] == 800.0  # 500 + 300 agregados
    assert acme["facturas_pendientes"] == 2
    assert acme["dias_desde_emision_max"] == (today - dt.date(2026, 5, 1)).days
    assert acme["fecha_emision"] == "01/05/2026"


def test_separa_por_ciudad(monkeypatch):
    today = dt.date(2026, 6, 30)
    docs = [
        _doc("001-002-000000001", "ACME SA", 500.0, "01/05/2026"),     # UIO
        _doc("001-001-000000002", "GYE CLIENTE", 200.0, "01/05/2026"),  # GYE
    ]
    _setup(monkeypatch, docs)
    assert [r["cliente"] for r in cc.clientes_sin_credito_con_saldo("GYE", fecha_referencia=today)] == ["GYE CLIENTE"]
    assert [r["cliente"] for r in cc.clientes_sin_credito_con_saldo("UIO", fecha_referencia=today)] == ["ACME SA"]


def test_ordena_por_saldo_desc_y_respeta_n(monkeypatch):
    today = dt.date(2026, 6, 30)
    docs = [
        _doc("001-002-000000001", "CHICO SA", 100.0, "01/06/2026"),
        _doc("001-002-000000002", "GRANDE SA", 900.0, "01/06/2026"),
        _doc("001-002-000000003", "MEDIO SA", 400.0, "01/06/2026"),
    ]
    _setup(monkeypatch, docs)
    top2 = cc.clientes_sin_credito_con_saldo("UIO", n=2, fecha_referencia=today)
    assert [r["cliente"] for r in top2] == ["GRANDE SA", "MEDIO SA"]
