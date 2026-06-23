"""Renderer de correo HTML unificado (Acción 6) — mata la repetición de HTML inline.

Outlook estripa los bloques <style>, así que TODO va con estilos inline + atributo
bgcolor (ver CLAUDE.md issue #3). Los reportes pasan DATOS (kpis, filas), no HTML; el
branding (color, logo) sale del tenant. Andamiaje aditivo: los reportes actuales
todavía arman su propio HTML — este módulo es la base para unificarlo sin romperlos.
"""
from __future__ import annotations

from collections.abc import Iterable
from html import escape


def kpi_card(label: str, value: str, *, color: str = "#2E7D32") -> str:
    """Una celda <td> de KPI (para colocar dentro de una fila de KPIs)."""
    return (
        f'<td bgcolor="{color}" '
        f'style="padding:12px 16px;color:#ffffff;font-family:Arial,sans-serif;'
        f'border-radius:6px;">'
        f'<div style="font-size:12px;opacity:0.85;">{escape(label)}</div>'
        f'<div style="font-size:22px;font-weight:700;">{escape(value)}</div>'
        f"</td>"
    )


def table(headers: list[str], rows: Iterable[Iterable[object]], *,
          header_bg: str = "#2E7D32") -> str:
    """Tabla HTML con cabecera coloreada y filas tipo cebra, todo inline."""
    head = "".join(
        f'<th bgcolor="{header_bg}" '
        f'style="padding:8px 10px;color:#fff;text-align:left;'
        f'font-family:Arial,sans-serif;font-size:13px;">{escape(str(h))}</th>'
        for h in headers
    )
    body_parts: list[str] = []
    for i, row in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f3f6f3"
        cells = "".join(
            f'<td bgcolor="{bg}" '
            f'style="padding:8px 10px;font-family:Arial,sans-serif;font-size:13px;'
            f'border-bottom:1px solid #e0e0e0;">{escape(str(c))}</td>'
            for c in row
        )
        body_parts.append(f"<tr>{cells}</tr>")
    return (
        '<table cellpadding="0" cellspacing="0" '
        'style="border-collapse:collapse;width:100%;">'
        f"<tr>{head}</tr>{''.join(body_parts)}</table>"
    )


def banner(text: str, *, color: str = "#ef6c00") -> str:
    """Aviso destacado (ej. 'datos parciales')."""
    return (
        f'<div style="margin:12px 0;padding:10px 14px;background:#fff3e0;'
        f'border-left:4px solid {color};font-family:Arial,sans-serif;'
        f'font-size:13px;color:#333;">{escape(text)}</div>'
    )


def document(title: str, body_html: str, *, brand_color: str = "#2E7D32",
             logo_url: str | None = None) -> str:
    """Envoltura de correo completo: header con branding del tenant + cuerpo."""
    logo = (
        f'<img src="{escape(logo_url)}" alt="" height="36" '
        f'style="display:block;border:0;">'
        if logo_url
        else ""
    )
    return (
        '<div style="max-width:680px;margin:0 auto;'
        'font-family:Arial,sans-serif;color:#222;">'
        f'<div bgcolor="{brand_color}" style="padding:16px 20px;color:#fff;">{logo}'
        f'<div style="font-size:18px;font-weight:700;">{escape(title)}</div></div>'
        f'<div style="padding:20px;">{body_html}</div>'
        "</div>"
    )
