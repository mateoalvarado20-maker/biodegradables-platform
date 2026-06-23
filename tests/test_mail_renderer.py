"""Tests del renderer de correo unificado (Acción 6).

Verifican que el HTML use estilos inline + bgcolor (Outlook-safe) y nunca un bloque
<style> (que Outlook estripa, CLAUDE.md issue #3), y que el contenido se escape.
"""
from __future__ import annotations

from core.mail import renderer


def test_kpi_card_inline_styles_no_style_block():
    html = renderer.kpi_card("Ventas", "$1.000", color="#123456")
    assert 'bgcolor="#123456"' in html
    assert "style=" in html
    assert "<style" not in html
    assert "Ventas" in html
    assert "$1.000" in html


def test_table_escapes_content_and_zebra():
    html = renderer.table(["A", "B"], [["1", "2"], ["3", "<x>"]])
    assert "<th" in html
    assert "<td" in html
    assert "&lt;x&gt;" in html      # contenido escapado
    assert "#f3f6f3" in html        # fila cebra
    assert "<style" not in html


def test_banner_and_document_wrap():
    b = renderer.banner("Datos parciales")
    assert "Datos parciales" in b
    doc = renderer.document(
        "Reporte diario", "<p>cuerpo</p>", brand_color="#2E7D32",
        logo_url="https://x/logo.png",
    )
    assert "Reporte diario" in doc
    assert "logo.png" in doc
    assert "<p>cuerpo</p>" in doc   # el body_html ya rendereado se inserta tal cual
    assert "<style" not in doc
