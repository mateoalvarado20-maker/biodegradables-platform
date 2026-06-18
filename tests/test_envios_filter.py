"""Regresión 2026-06-18: el filtro de envíos de José contaba "TRANSPARENTE"
(vaso/funda/tazón transparente) como ítem de transporte porque buscaba el
substring "TRANSP". Resultado: clientes sin envío aparecían en su ruta.
El fix exige "TRANSP." (con punto) o "TRANSPORTE", que no matchean "TRANSPARENTE".
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import contifico_client as c


def _doc(*nombres, codigos=None):
    codigos = codigos or [""] * len(nombres)
    return {"detalles": [
        {"producto_nombre": n, "producto_codigo": cod}
        for n, cod in zip(nombres, codigos)
    ]}


def test_envio_real_transp_ext():
    assert c._tiene_transporte_item(_doc("TRANSP. EXT.-CB. 12%")) is True
    assert c._tiene_transporte_item(_doc("TRANSP. EXT. 12%")) is True
    assert c._tiene_transporte_item(_doc("TRANSP. B.E.")) is True
    assert c._tiene_transporte_item(_doc("TRANSPORTE")) is True


def test_transparente_NO_es_envio():
    # Los falsos positivos reales que metían clientes sin envío a la ruta de José.
    assert c._tiene_transporte_item(_doc("Vaso 12 oz transparente")) is False
    assert c._tiene_transporte_item(_doc("Funda Doypack zipper, cara transparente, 18 x 26 cm")) is False
    assert c._tiene_transporte_item(_doc("Tazón base ancha + tapa transparente, 16oz")) is False


def test_codigo_transporte():
    assert c._tiene_transporte_item(_doc("Producto X", codigos=["TRANSP01"])) is True


def test_mix_transparente_y_transporte_real():
    # Si la factura tiene producto transparente Y envío real → sí es envío.
    assert c._tiene_transporte_item(
        _doc("Vaso 16 oz transparente", "TRANSP. EXT.-CB. 12%")
    ) is True


def test_sin_detalles():
    assert c._tiene_transporte_item({"detalles": []}) is False
    assert c._tiene_transporte_item({}) is False
