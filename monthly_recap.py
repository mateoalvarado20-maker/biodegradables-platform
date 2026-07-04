"""Monthly recap emails (Phase M — 2026-06-02).

Genera 2 correos mensuales que se mandan a Daniel + Gabriela cada día 1 del
mes siguiente:

1. **Sales recap**: ventas del mes recién terminado + 3 proyecciones para el
   mes siguiente (optimista/probable/pesimista) basadas en histórico y
   factores externos del news_brief.

2. **Activities recap**: por cada colaborador, qué actividades cumplió y
   cuáles no. Semáforo por priority. Total cumplimiento del mes.

Scheduler: día 1 de cada mes, 9 AM EC (sales) y 10 AM EC (activities).

Ambos llaman a `graph_mail.send` desde el bot user (malvarado@), enviando a
los supervisores configurados.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import activity_state
import contifico_client
import core_config
import forecasting
import graph_mail
import news_brief

LOCAL_TZ = timezone(timedelta(hours=-5))

# Destinatarios fijos del recap mensual — sin per-user override (es resumen
# del equipo entero, va siempre a gerencia).
RECAP_TO = [
    e.strip() for e in os.environ.get(
        "MONTHLY_RECAP_TO",
        ",".join(core_config.JEFE),
    ).split(",")
    if e.strip()
]

RECAP_FROM = os.environ.get("TRACKER_TARGET_USER", core_config.MIO).strip()

MESES_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _previous_month(today: date | None = None) -> tuple[int, int]:
    """Devuelve (year, month) del mes anterior al today."""
    t = today or date.today()
    if t.month == 1:
        return (t.year - 1, 12)
    return (t.year, t.month - 1)


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return (year + 1, 1)
    return (year, month + 1)


# ===================== SALES RECAP =====================
def _build_sales_recap_html(year: int, month: int) -> str:
    """HTML del recap mensual de ventas para el mes (year, month) ya cerrado."""
    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    period_start = date(year, month, 1)
    period_end = date(year, month, last_day)

    def _safe(fn, default):
        try:
            return fn()
        except Exception:
            return default

    # Pull data del mes — TODO SIN IVA (subtotal)
    ventas_mes = _safe(lambda: contifico_client.ventas_rango(period_start, period_end),
                       {"subtotal": 0, "num_facturas": 0, "clientes_unicos": 0})
    top_clientes = _safe(lambda: contifico_client.top_clientes(period_start, period_end, n=10), [])
    top_vendedores = _safe(lambda: contifico_client.top_vendedores(period_start, period_end, n=5), [])
    ventas_ciudad = _safe(lambda: contifico_client.ventas_por_ciudad(period_start, period_end),
                          {"por_ciudad": {}})
    top_cli_ciudad = _safe(lambda: contifico_client.top_clientes_por_ciudad(period_start, period_end, n=10),
                           {"UIO": [], "GYE": []})
    top_prod_ciudad = _safe(lambda: contifico_client.top_productos_por_ciudad(period_start, period_end, n=10),
                            {"UIO": [], "GYE": []})
    unidades = _safe(lambda: contifico_client.unidades_vendidas(period_start, period_end),
                     {"unidades": 0, "productos_unicos": 0})
    rango = _safe(lambda: contifico_client.clientes_por_rango(period_start, period_end),
                  {"rangos": [], "total_ventas": 0, "total_clientes": 0})
    actividad = _safe(lambda: contifico_client.clientes_actividad(period_end),
                      {"activos": 0, "inactivos": 0, "inactivos_lista": []})

    # Crecimiento del MES (YoY: este mes vs mismo mes año anterior)
    py_year = year - 1
    py_start = date(py_year, month, 1)
    py_end = date(py_year, month, monthrange(py_year, month)[1])
    ventas_py = _safe(lambda: contifico_client.ventas_rango(py_start, py_end), {"subtotal": 0})

    total_mes = ventas_mes.get("subtotal") or 0
    total_py = ventas_py.get("subtotal") or 0
    growth_yoy = (
        ((total_mes - total_py) / total_py * 100)
        if total_py > 0 else None
    )

    # Crecimiento del AÑO (YTD: enero→mes cerrado, vs mismo período año anterior)
    ytd_cur = _safe(lambda: contifico_client.ventas_rango(date(year, 1, 1), period_end), {"subtotal": 0})
    ytd_py = _safe(lambda: contifico_client.ventas_rango(date(py_year, 1, 1), py_end), {"subtotal": 0})
    ytd_cur_v = ytd_cur.get("subtotal") or 0
    ytd_py_v = ytd_py.get("subtotal") or 0
    growth_ytd = ((ytd_cur_v - ytd_py_v) / ytd_py_v * 100) if ytd_py_v > 0 else None

    # % de ventas concentrado en el top 10 de clientes (global, neto)
    top10_total = sum((c.get("total") or 0) for c in top_clientes[:10])
    pct_top10 = (top10_total / total_mes * 100) if total_mes > 0 else 0.0

    # Proyecciones del mes SIGUIENTE
    next_year, next_month = _next_month(year, month)
    try:
        forecast_next = forecasting.forecast_baseline(next_year, next_month, neto=True)
    except Exception as e:
        forecast_next = {"error": str(e)}

    # Proyección que se hizo PARA el mes cerrado (mismo método baseline del
    # recap anterior) — para comparar en qué escenario cayó el real.
    try:
        forecast_propio = forecasting.forecast_baseline(year, month, neto=True)
    except Exception as e:
        forecast_propio = {"error": str(e)}

    # News brief context (para enriquecer las proyecciones)
    brief = news_brief.load_brief()
    brief_context = ""
    if brief and not brief.get("error"):
        sections = []
        for key, label in [
            ("economia_ecuador", "🇪🇨 Economía Ecuador"),
            ("geopolitica_supply", "🌎 Geopolítica / supply"),
            ("sector_industria", f"📦 Sector {core_config.COMPANY_SECTOR}"),
        ]:
            points = brief.get(key) or []
            if points:
                lis = "".join(f"<li>{p}</li>" for p in points[:3])
                sections.append(f"<b>{label}:</b><ul>{lis}</ul>")
        if sections:
            brief_context = "<div style='font-size:12px;color:#555;'>" + "".join(sections) + "</div>"

    # Construir HTML
    nombre_mes = MESES_ES[month - 1].capitalize()
    nombre_mes_next = MESES_ES[next_month - 1].capitalize()

    # Top tablas
    vendedores_rows = "".join(
        f'<tr><td>{v.get("vendedor", "—")}</td>'
        f'<td style="text-align:right;">${v.get("total", 0):,.0f}</td>'
        f'<td style="text-align:right;color:#888;">{v.get("num_facturas", 0)}</td></tr>'
        for v in top_vendedores[:5]
    )

    # Ciudad
    ciudad_data = ventas_ciudad.get("por_ciudad", {})
    uio = ciudad_data.get("UIO", {})
    gye = ciudad_data.get("GYE", {})

    # Proyecciones
    if forecast_next.get("error"):
        forecast_html = (
            f'<p style="color:#c62828;">⚠️ No pude generar proyección para '
            f'{nombre_mes_next}: {forecast_next.get("error")}</p>'
        )
    else:
        proy = forecast_next.get("proyeccion", {})
        forecast_html = (
            f'<table>'
            f'<tr><th>Escenario</th><th style="text-align:right;">Ventas esperadas</th></tr>'
            f'<tr style="background:#ffebee;"><td>🔴 Pesimista</td>'
            f'<td style="text-align:right;"><b>${proy.get("pesimista", 0):,.0f}</b></td></tr>'
            f'<tr style="background:#fff3e0;"><td>🟡 Probable</td>'
            f'<td style="text-align:right;"><b>${proy.get("probable", 0):,.0f}</b></td></tr>'
            f'<tr style="background:#e8f5e9;"><td>🟢 Optimista</td>'
            f'<td style="text-align:right;"><b>${proy.get("optimista", 0):,.0f}</b></td></tr>'
            f'</table>'
            f'<p style="color:#666;font-size:12px;">'
            f'Baseline = mismo mes año anterior × (1 + crecimiento YoY de {forecast_next.get("yoy_growth_estimado_pct", 0):.0f}%). '
            f'Rango ± 15%. Mismo mes {py_year}: ${forecast_next.get("ventas_mismo_mes_anio_anterior", 0):,.0f}.</p>'
        )

    # === Proyección vs real del mes cerrado ===
    proy_vs_real_html = ""
    if not forecast_propio.get("error"):
        pv = forecast_propio.get("proyeccion", {})
        pes = pv.get("pesimista", 0) or 0
        prob = pv.get("probable", 0) or 0
        opt = pv.get("optimista", 0) or 0
        if total_mes < pes:
            veredicto = (
                f"quedó <b>por debajo del escenario pesimista</b> "
                f"(faltaron ${pes - total_mes:,.0f} para alcanzarlo)"
            )
            v_color = "#c62828"
        elif total_mes < prob:
            veredicto = "cayó <b>entre el escenario pesimista y el probable</b>"
            v_color = "#ef6c00"
        elif total_mes < opt:
            veredicto = "cayó <b>entre el escenario probable y el optimista</b>"
            v_color = "#2e7d32"
        else:
            veredicto = (
                f"<b>superó el escenario optimista</b> "
                f"(${total_mes - opt:,.0f} por encima) 🎉"
            )
            v_color = "#0e7c39"

        def _esc_row(emoji, nombre_esc, valor, bg):
            alcanzado = (
                '<span style="color:#2e7d32;font-weight:700;">✔ superado</span>'
                if total_mes >= valor else
                '<span style="color:#c62828;">✘ no alcanzado</span>'
            )
            return (
                f'<tr style="background:{bg};"><td>{emoji} {nombre_esc}</td>'
                f'<td style="text-align:right;">${valor:,.0f}</td>'
                f'<td style="text-align:right;">{alcanzado}</td></tr>'
            )

        proy_vs_real_html = (
            f'<h3>🎯 Proyección vs real — {nombre_mes}</h3>'
            f'<table>'
            f'<tr><th>Escenario proyectado</th>'
            f'<th style="text-align:right;">Ventas esperadas</th>'
            f'<th style="text-align:right;">Resultado</th></tr>'
            f'{_esc_row("🔴", "Pesimista", pes, "#ffebee")}'
            f'{_esc_row("🟡", "Probable", prob, "#fff3e0")}'
            f'{_esc_row("🟢", "Optimista", opt, "#e8f5e9")}'
            f'</table>'
            f'<p style="font-size:14px;">Ventas reales de {nombre_mes}: '
            f'<b style="color:{v_color};">${total_mes:,.0f}</b> — el mes '
            f'<span style="color:{v_color};">{veredicto}</span>.</p>'
            f'<p style="color:#888;font-size:11px;margin:2px 0 0;">'
            f'Proyección baseline recalculada con el mismo método del recap '
            f'anterior (mismo mes año anterior × crecimiento YoY, ±15%); '
            f'puede variar levemente respecto del correo del mes pasado.</p>'
        )

    def _growth_card(titulo, sub, cur_label, cur_v, prev_label, prev_v, pct):
        if pct is None:
            badge = '<span style="color:#888;font-size:24px;font-weight:800;">s/d</span>'
            note = f'Sin datos de {prev_label} para comparar.'
        else:
            col = "#2e7d32" if pct > 0 else "#c62828"
            arr = "▲" if pct > 0 else "▼"
            verbo = "más" if pct > 0 else "menos"
            badge = (f'<span style="color:{col};font-size:26px;font-weight:800;">'
                     f'{arr} {abs(pct):.1f}%</span>')
            note = f'Vendimos {abs(pct):.1f}% {verbo} que {prev_label}.'
        return (
            f'<td width="50%" valign="top" bgcolor="#f4f8f4" '
            f'style="padding:14px 16px;border:1px solid #d9e6d9;border-radius:8px;">'
            f'<div style="font-size:11px;color:#5e6b5e;text-transform:uppercase;'
            f'font-weight:700;letter-spacing:.5px;">{titulo}</div>'
            f'<div style="font-size:12px;color:#888;margin-bottom:8px;">{sub}</div>'
            f'<div style="margin:4px 0;">{badge}</div>'
            f'<div style="font-size:13px;color:#333;margin-top:10px;">'
            f'{cur_label}: <b>${cur_v:,.0f}</b></div>'
            f'<div style="font-size:13px;color:#888;">{prev_label}: ${prev_v:,.0f}</div>'
            f'<div style="font-size:12px;color:#555;margin-top:8px;">{note}</div>'
            f'</td>'
        )

    mes_card = _growth_card(
        "Crecimiento del mes",
        f"{nombre_mes} {year} vs {nombre_mes} {py_year}",
        f"{nombre_mes} {year}", total_mes,
        f"{nombre_mes} {py_year}", total_py,
        growth_yoy,
    )
    anio_card = _growth_card(
        "Crecimiento del año",
        f"Acumulado ene–{nombre_mes} {year} vs ene–{nombre_mes} {py_year}",
        f"Ene–{nombre_mes} {year}", ytd_cur_v,
        f"Ene–{nombre_mes} {py_year}", ytd_py_v,
        growth_ytd,
    )

    # Top 10 clientes por ciudad
    def _cli_rows(lst):
        return "".join(
            f'<tr><td>{c.get("cliente","—")}</td>'
            f'<td style="text-align:right;">${c.get("total",0):,.0f}</td>'
            f'<td style="text-align:right;color:#888;">{c.get("num_facturas",0)}</td></tr>'
            for c in lst[:10]
        ) or '<tr><td colspan="3" style="color:#888;">Sin datos</td></tr>'
    cli_uio_rows = _cli_rows(top_cli_ciudad.get("UIO", []))
    cli_gye_rows = _cli_rows(top_cli_ciudad.get("GYE", []))

    # Top 10 productos por ciudad
    def _prod_rows(lst):
        return "".join(
            f'<tr><td>{p.get("producto","—")}</td>'
            f'<td style="text-align:right;">{p.get("cantidad",0):,.0f}</td>'
            f'<td style="text-align:right;">${p.get("revenue",0):,.0f}</td></tr>'
            for p in lst[:10]
        ) or '<tr><td colspan="3" style="color:#888;">Sin datos</td></tr>'
    prod_uio_rows = _prod_rows(top_prod_ciudad.get("UIO", []))
    prod_gye_rows = _prod_rows(top_prod_ciudad.get("GYE", []))

    # Leyenda de la dona (rangos de cliente)
    _dona_colors = {">$1000": "#0e7c39", "$500–1000": "#43a047",
                    "$100–500": "#9ccc65", "<$100": "#e0e0e0"}
    rango_rows = "".join(
        f'<tr><td><span style="display:inline-block;width:11px;height:11px;'
        f'background:{_dona_colors.get(r["rango"], "#999")};border-radius:2px;margin-right:6px;"></span>'
        f'{r["rango"]}</td>'
        f'<td style="text-align:right;">{r["clientes"]}</td>'
        f'<td style="text-align:right;">${r["ventas"]:,.0f}</td>'
        f'<td style="text-align:right;font-weight:600;">{r["pct_ventas"]:.1f}%</td></tr>'
        for r in rango.get("rangos", [])
    )

    # Inactivos (lista corta para no saturar)
    inact_lista = actividad.get("inactivos_lista", [])
    inact_preview = ", ".join(inact_lista[:15])
    if len(inact_lista) > 15:
        inact_preview += f" … (+{len(inact_lista) - 15} más)"

    # Tickets por ciudad (ya vienen netos)
    uio_ticket = uio.get("ticket_promedio", 0)
    gye_ticket = gye.get("ticket_promedio", 0)

    css = """
    body{font-family:'Segoe UI',Arial,sans-serif;color:#2c2c2c;max-width:780px;margin:0;padding:18px;}
    h2{color:#0e7c39;border-bottom:2px solid #0e7c39;padding-bottom:8px;margin-top:0;}
    h3{color:#0e7c39;margin-top:24px;margin-bottom:8px;}
    table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px;}
    th{background:#0e7c39;color:white;text-align:left;padding:8px 10px;}
    td{border-bottom:1px solid #ececec;padding:8px 10px;}
    .kpi-box{background:#f4faf6;border:1px solid #d9e0d9;padding:12px;border-radius:6px;margin:6px 0;}
    .footer{font-size:11px;color:#888;margin-top:30px;border-top:1px solid #eee;padding-top:10px;}
    """

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>

<h2>📊 Recap de ventas — {nombre_mes} {year}</h2>
<p>Hola Daniel y Gabriela,<br>acá el resumen comercial del mes recién cerrado y
las proyecciones para {nombre_mes_next}.</p>

<p style="color:#888;font-size:12px;margin:2px 0 0;">Todos los montos son <b>sin IVA</b> (subtotal).</p>

<h3>💰 Ventas del mes</h3>
<div class="kpi-box">
  <div style="font-size:11px;color:#5e6b5e;text-transform:uppercase;">Total facturado (sin IVA)</div>
  <div style="font-size:28px;font-weight:700;color:#0e7c39;">
    ${total_mes:,.0f}
  </div>
  <div style="color:#555;font-size:13px;margin-top:6px;">
    {ventas_mes.get("num_facturas", 0)} facturas · Ticket promedio
    ${ventas_mes.get("ticket_promedio", 0):,.0f} ·
    {ventas_mes.get("clientes_unicos", 0)} clientes únicos
  </div>
  <div style="color:#555;font-size:13px;margin-top:6px;">
    📦 <b>{unidades.get("unidades", 0):,.0f}</b> unidades vendidas ·
    {unidades.get("productos_unicos", 0)} productos distintos
  </div>
</div>

<h3>📈 Crecimiento</h3>
<table width="100%" style="border-collapse:separate;border-spacing:10px 0;">
<tr>{mes_card}{anio_card}</tr>
</table>
<p style="color:#888;font-size:11px;margin:4px 0 0;">
  <b>Mes</b> = {nombre_mes} {year} comparado con {nombre_mes} {py_year}. ·
  <b>Año</b> = todo lo vendido de enero a {nombre_mes} {year}, comparado con el
  mismo tramo de {py_year}.</p>

{proy_vs_real_html}

<h3>🏙️ Por ciudad</h3>
<table>
<tr><th>Ciudad</th><th style="text-align:right;">Ventas</th><th style="text-align:right;">Facturas</th><th style="text-align:right;">Ticket promedio</th></tr>
<tr><td>Quito (UIO)</td>
    <td style="text-align:right;">${uio.get("total", 0):,.0f}</td>
    <td style="text-align:right;color:#888;">{uio.get("num_facturas", 0)}</td>
    <td style="text-align:right;">${uio_ticket:,.0f}</td></tr>
<tr><td>Guayaquil (GYE)</td>
    <td style="text-align:right;">${gye.get("total", 0):,.0f}</td>
    <td style="text-align:right;color:#888;">{gye.get("num_facturas", 0)}</td>
    <td style="text-align:right;">${gye_ticket:,.0f}</td></tr>
<tr style="background:#f4faf6;font-weight:700;border-top:2px solid #0e7c39;">
    <td>TOTAL</td>
    <td style="text-align:right;color:#0e7c39;">${(uio.get("total", 0) + gye.get("total", 0)):,.0f}</td>
    <td style="text-align:right;">{uio.get("num_facturas", 0) + gye.get("num_facturas", 0)}</td>
    <td style="text-align:right;">${((uio.get("total", 0) + gye.get("total", 0)) / (uio.get("num_facturas", 0) + gye.get("num_facturas", 0)) if (uio.get("num_facturas", 0) + gye.get("num_facturas", 0)) > 0 else 0):,.0f}</td></tr>
</table>

<h3>🍩 Clientes por rango de compra (sin IVA)</h3>
<p style="color:#555;font-size:13px;margin:4px 0;">
  Cada gajo de la dona es el <b>% de ventas</b> que aporta cada rango; entre
  paréntesis, cuántos clientes hay en ese rango.</p>
<div style="text-align:center;margin:8px 0;"><img src="cid:chart_dona" alt="Clientes por rango" style="max-width:420px;width:100%;"></div>
<table>
<tr><th>Rango (compra del mes)</th><th style="text-align:right;">Clientes</th><th style="text-align:right;">Ventas</th><th style="text-align:right;">% ventas</th></tr>
{rango_rows or '<tr><td colspan="4" style="color:#888;">Sin datos</td></tr>'}
</table>

<h3>🎯 Concentración de ventas</h3>
<div class="kpi-box">
  Nuestros <b>top 10 clientes</b> concentran el
  <span style="font-size:20px;font-weight:700;color:#0e7c39;">{pct_top10:.1f}%</span>
  de las ventas del mes.
</div>

<h3>👥 Top 10 clientes — Quito (UIO)</h3>
<table>
<tr><th>Cliente</th><th style="text-align:right;">Ventas</th><th style="text-align:right;">Facturas</th></tr>
{cli_uio_rows}
</table>

<h3>👥 Top 10 clientes — Guayaquil (GYE)</h3>
<table>
<tr><th>Cliente</th><th style="text-align:right;">Ventas</th><th style="text-align:right;">Facturas</th></tr>
{cli_gye_rows}
</table>

<h3>📦 Top 10 productos — Quito (UIO)</h3>
<table>
<tr><th>Producto</th><th style="text-align:right;">Unidades</th><th style="text-align:right;">Venta</th></tr>
{prod_uio_rows}
</table>

<h3>📦 Top 10 productos — Guayaquil (GYE)</h3>
<table>
<tr><th>Producto</th><th style="text-align:right;">Unidades</th><th style="text-align:right;">Venta</th></tr>
{prod_gye_rows}
</table>

<h3>🔄 Clientes activos / inactivos (últimos 3 meses)</h3>
<div class="kpi-box">
  ✅ <b style="color:#2e7d32;">{actividad.get("activos", 0)}</b> clientes activos
  (facturaron en los últimos 3 meses) ·
  ⚠️ <b style="color:#c62828;">{actividad.get("inactivos", 0)}</b> se enfriaron
  (compraban antes, sin compras en los últimos 3 meses).
  {f'<div style="font-size:12px;color:#777;margin-top:6px;">Inactivos: {inact_preview}</div>' if inact_preview else ''}
</div>

<h3>🏆 Top 5 vendedores</h3>
<table>
<tr><th>Vendedor</th><th style="text-align:right;">Ventas</th><th style="text-align:right;">Facturas</th></tr>
{vendedores_rows or '<tr><td colspan="3" style="color:#888;">Sin datos</td></tr>'}
</table>

<h3>🔮 Proyecciones para {nombre_mes_next} {next_year}</h3>
{forecast_html}

{f'<h3>🌎 Contexto considerado</h3>{brief_context}' if brief_context else ''}

<p style="color:#666;font-size:12px;font-style:italic;margin-top:20px;">
⚠️ Las proyecciones son escenarios razonados basados en histórico + contexto
actual, NO predicciones exactas. Útiles para planificación, no para targets
duros. Para análisis más profundo de un escenario específico, hablar con el
Data Bot.
</p>

<div class="footer">
Recap mensual automático generado por el Activities Bot · {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")} EC
</div>

</body></html>"""


def _generate_donut_png(rangos: list[dict]) -> bytes | None:
    """Dona de % de ventas por rango de cliente. None si matplotlib no está
    disponible o si no hay datos (el correo igual lleva la tabla)."""
    data = [r for r in (rangos or []) if (r.get("ventas") or 0) > 0]
    if not data:
        return None
    try:
        import io

        import matplotlib
        matplotlib.use("Agg")  # sin GUI
        import matplotlib.pyplot as plt

        colors_map = {">$1000": "#0e7c39", "$500–1000": "#43a047",
                      "$100–500": "#9ccc65", "<$100": "#bdbdbd"}
        labels = [f'{r["rango"]} ({r["clientes"]} cli.)' for r in data]
        sizes = [r["ventas"] for r in data]
        colors = [colors_map.get(r["rango"], "#999999") for r in data]
        fig, ax = plt.subplots(figsize=(5.4, 3.8), dpi=110)
        wedges, _t, autotexts = ax.pie(
            sizes, colors=colors, startangle=90, counterclock=False,
            autopct=lambda p: f"{p:.0f}%", pctdistance=0.78,
            wedgeprops=dict(width=0.42, edgecolor="white"),
        )
        for at in autotexts:
            at.set_color("#222222")
            at.set_fontsize(9)
            at.set_fontweight("bold")
        ax.legend(wedges, labels, loc="center left", bbox_to_anchor=(0.95, 0.5),
                  fontsize=8, frameon=False)
        ax.axis("equal")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def send_sales_recap(year: int | None = None, month: int | None = None) -> dict:
    """Envía el sales recap del mes especificado (default: mes anterior)."""
    from calendar import monthrange
    if year is None or month is None:
        year, month = _previous_month()
    nombre_mes = MESES_ES[month - 1].capitalize()
    html = _build_sales_recap_html(year, month)
    subject = f"📊 Recap de ventas — {nombre_mes} {year}"

    # Dona de clientes por rango (mismo período = mes cerrado). Cache de
    # get_documentos hace que este pull reuse el del HTML.
    inline_images = None
    try:
        last_day = monthrange(year, month)[1]
        rango = contifico_client.clientes_por_rango(
            date(year, month, 1), date(year, month, last_day))
        png = _generate_donut_png(rango.get("rangos", []))
        if png:
            inline_images = [{
                "name": "dona_clientes.png",
                "content_bytes": png,
                "content_id": "chart_dona",
                "content_type": "image/png",
            }]
    except Exception:
        inline_images = None

    graph_mail.send_email(
        RECAP_TO,
        subject,
        html,
        from_user=RECAP_FROM,
        inline_images=inline_images,
    )
    return {"ok": True, "to": RECAP_TO, "subject": subject, "period": f"{year}-{month:02d}",
            "con_grafico": bool(inline_images)}


# ===================== MIDMONTH STATUS (Quincenal — día 15) =====================
def _build_midmonth_status_html(year: int | None = None, month: int | None = None) -> str:
    """HTML del estado mid-month (día 15): ventas MTD vs proyección original
    + projection refresh para fin de mes basado en run-rate actual.

    Phase M.5 (2026-06-02): permite a Daniel/Gabriela ver el día 15 si la
    proyección del mes va alineada con la realidad.
    """
    from calendar import monthrange

    today = date.today()
    year = year or today.year
    month = month or today.month
    last_day_month = monthrange(year, month)[1]
    period_start = date(year, month, 1)
    days_elapsed = today.day  # asumiendo que hoy es ~15

    # MTD del mes actual
    try:
        ventas_mtd = contifico_client.ventas_rango(period_start, today)
    except Exception as e:
        ventas_mtd = {"error": str(e), "total": 0}

    # Mismo período del año anterior (mismos días)
    py_year = year - 1
    py_end_day = min(today.day, monthrange(py_year, month)[1])
    try:
        ventas_py_same_period = contifico_client.ventas_rango(
            date(py_year, month, 1), date(py_year, month, py_end_day)
        )
    except Exception:
        ventas_py_same_period = {"total": 0}

    # Proyección original del forecasting
    try:
        forecast = forecasting.forecast_baseline(year, month)
    except Exception as e:
        forecast = {"error": str(e), "proyeccion": {"probable": 0}}

    probable_inicial = forecast.get("proyeccion", {}).get("probable", 0) or 0
    pesimista_inicial = forecast.get("proyeccion", {}).get("pesimista", 0) or 0
    optimista_inicial = forecast.get("proyeccion", {}).get("optimista", 0) or 0

    # Expected MTD asumiendo distribución uniforme de la probable
    expected_mtd = probable_inicial * (days_elapsed / last_day_month)
    actual_mtd = ventas_mtd.get("total", 0) or 0
    pace_pct = (actual_mtd / expected_mtd * 100) if expected_mtd > 0 else None

    # Run-rate ajustada → proyección refresh para todo el mes
    run_rate = (actual_mtd / days_elapsed) if days_elapsed > 0 else 0
    proy_refresh = run_rate * last_day_month

    # vs PY
    growth_yoy = None
    py_total = ventas_py_same_period.get("total", 0) or 0
    if py_total > 0:
        growth_yoy = (actual_mtd - py_total) / py_total * 100

    nombre_mes = MESES_ES[month - 1].capitalize()

    # Decidir color del semáforo
    # Fase 3 (auditoría R3): pace_pct=None crasheaba el f-string {pace_pct:.0f}
    # del HTML — pace_str lo hace seguro.
    pace_str = f"{pace_pct:.0f}%" if pace_pct is not None else "n/d"
    if pace_pct is None:
        pace_color = "#888"
        pace_emoji = "⚪"
    elif pace_pct >= 95:
        pace_color = "#2e7d32"
        pace_emoji = "🟢"
    elif pace_pct >= 75:
        pace_color = "#ef6c00"
        pace_emoji = "🟡"
    else:
        pace_color = "#c62828"
        pace_emoji = "🔴"

    growth_html = ""
    if growth_yoy is not None:
        color = "#2e7d32" if growth_yoy > 0 else "#c62828"
        arrow = "▲" if growth_yoy > 0 else "▼"
        growth_html = (
            f' <span style="color:{color};font-weight:600;">'
            f'{arrow} {abs(growth_yoy):.1f}% vs mismo período {py_year}</span>'
        )

    # Diferencia entre proyección refresh y proyección original probable
    delta_proy = proy_refresh - probable_inicial
    if abs(delta_proy) < probable_inicial * 0.05:
        # menos de 5% de diferencia → alineada
        proy_status = (
            f'<span style="color:#2e7d32;font-weight:600;">✅ ALINEADA</span> '
            f'(diferencia &lt;5% con la proyección original)'
        )
    elif delta_proy > 0:
        proy_status = (
            f'<span style="color:#2e7d32;font-weight:600;">▲ SUPERANDO</span> '
            f'(${delta_proy:,.0f} sobre la proyección probable)'
        )
    else:
        proy_status = (
            f'<span style="color:#c62828;font-weight:600;">▼ POR DEBAJO</span> '
            f'(${abs(delta_proy):,.0f} bajo la proyección probable)'
        )

    css = """
    body{font-family:'Segoe UI',Arial,sans-serif;color:#2c2c2c;max-width:780px;margin:0;padding:18px;}
    h2{color:#0e7c39;border-bottom:2px solid #0e7c39;padding-bottom:8px;margin-top:0;}
    h3{color:#0e7c39;margin-top:24px;margin-bottom:8px;}
    table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px;}
    th{background:#0e7c39;color:white;text-align:left;padding:8px 10px;}
    td{border-bottom:1px solid #ececec;padding:8px 10px;}
    .kpi-box{background:#f4faf6;border:1px solid #d9e0d9;padding:14px;border-radius:6px;margin:8px 0;}
    .footer{font-size:11px;color:#888;margin-top:30px;border-top:1px solid #eee;padding-top:10px;}
    """

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>

<h2>📈 Check-point quincenal — {nombre_mes} {year}</h2>
<p>Hola Daniel y Gabriela,<br>
hoy {today.strftime("%d/%m")} es día 15 — momento de validar si la proyección
del mes va alineada con la realidad.</p>

<h3>💰 Ventas MTD ({days_elapsed} de {last_day_month} días)</h3>
<div class="kpi-box">
  <div style="font-size:11px;color:#5e6b5e;text-transform:uppercase;">Vendido hasta hoy</div>
  <div style="font-size:28px;font-weight:700;color:#0e7c39;">
    ${actual_mtd:,.0f}{growth_html}
  </div>
  <div style="color:#555;font-size:13px;margin-top:6px;">
    {ventas_mtd.get("num_facturas", 0)} facturas · ticket promedio
    ${ventas_mtd.get("ticket_promedio", 0):,.0f}
  </div>
</div>

<h3>🎯 Ritmo vs proyección original</h3>
<table>
<tr><th>Métrica</th><th style="text-align:right;">Valor</th></tr>
<tr><td>Proyección PROBABLE del mes (día 1)</td>
    <td style="text-align:right;">${probable_inicial:,.0f}</td></tr>
<tr><td>Esperado para el día {days_elapsed} (proporcional)</td>
    <td style="text-align:right;">${expected_mtd:,.0f}</td></tr>
<tr><td>Real MTD</td>
    <td style="text-align:right;"><b>${actual_mtd:,.0f}</b></td></tr>
<tr style="background:#f4faf6;"><td>Ritmo / pace</td>
    <td style="text-align:right;color:{pace_color};font-weight:600;">
        {pace_emoji} {pace_str}
    </td></tr>
</table>

<h3>🔮 Proyección refresh (basada en run-rate actual)</h3>
<table>
<tr><th>Métrica</th><th style="text-align:right;">Valor</th></tr>
<tr><td>Run-rate diaria actual</td>
    <td style="text-align:right;">${run_rate:,.0f}/día</td></tr>
<tr><td>Proyección refrescada (día 1 actual × {last_day_month} días)</td>
    <td style="text-align:right;"><b>${proy_refresh:,.0f}</b></td></tr>
<tr><td>Proyección original PROBABLE (día 1)</td>
    <td style="text-align:right;color:#888;">${probable_inicial:,.0f}</td></tr>
<tr style="background:#f4faf6;"><td>Estado</td>
    <td style="text-align:right;">{proy_status}</td></tr>
</table>

<p style="color:#666;font-size:12px;font-style:italic;margin-top:20px;">
La <b>proyección refresh</b> asume que el ritmo de la primera quincena se
mantiene hasta fin de mes. En la práctica suele variar — esto es un
indicador, no un compromiso. Rango original: ${pesimista_inicial:,.0f} (pesimista)
– ${optimista_inicial:,.0f} (optimista).
</p>

<div class="footer">
Check-point quincenal generado automáticamente · {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")} EC
</div>

</body></html>"""


def send_midmonth_status(year: int | None = None, month: int | None = None) -> dict:
    """Envía el midmonth status (día 15)."""
    if year is None or month is None:
        t = date.today()
        year, month = t.year, t.month
    nombre_mes = MESES_ES[month - 1].capitalize()
    html = _build_midmonth_status_html(year, month)
    subject = f"📈 Check-point quincenal — {nombre_mes} {year}"
    graph_mail.send(
        from_user=RECAP_FROM,
        to=RECAP_TO,
        subject=subject,
        html_body=html,
    )
    return {"ok": True, "to": RECAP_TO, "subject": subject, "period": f"{year}-{month:02d}"}


# ===================== ACTIVITIES RECAP =====================
def _semaforo_emoji(pct: float | None, priority: str = "media") -> str:
    """Devuelve emoji semáforo. Más estricto para priority=alta."""
    if pct is None:
        return "⚪"
    threshold_ok = 85.0 if priority == "alta" else 70.0
    threshold_warn = 60.0 if priority == "alta" else 40.0
    if pct >= threshold_ok:
        return "🟢"
    if pct >= threshold_warn:
        return "🟡"
    return "🔴"


def _build_activities_recap_html(year: int, month: int) -> str:
    """HTML del recap mensual de actividades por colaborador."""
    nombre_mes = MESES_ES[month - 1].capitalize()
    known_users = activity_state.list_known_users()

    user_blocks = []
    for email in known_users:
        # Pseudo-users `unidentified-*` (email aislado del bot) no van en el recap
        if email.lower().startswith("unidentified-"):
            continue
        summary = activity_state.get_user_months_summary(email, year, month)
        if not summary["actividades_diarias"] and not summary["actividades_semanales"]:
            continue
        alias = email.split("@")[0]

        # Tabla daily
        daily_rows = ""
        cumplido_total = 0
        cumplido_alta = 0
        total_dailies = 0
        total_dailies_alta = 0
        for aid, agg in summary["actividades_diarias"].items():
            total = agg["total_marcado"]
            meta_diaria = agg.get("meta_diaria")
            dias_marcados = agg["dias_marcados"]
            dias_no_hechas = agg["dias_no_hechas"]
            priority = agg.get("priority", "media")
            if meta_diaria and dias_marcados > 0:
                meta_total = meta_diaria * dias_marcados
                pct = (total / meta_total * 100) if meta_total else None
            else:
                pct = None
            semaforo = _semaforo_emoji(pct, priority)
            prio_badge = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(priority, "")
            pct_txt = f"{pct:.0f}%" if pct is not None else "—"
            daily_rows += (
                f'<tr><td>{agg["nombre"]} {prio_badge}</td>'
                f'<td style="text-align:right;">{total:.0f}</td>'
                f'<td style="text-align:right;color:#888;">{dias_marcados}</td>'
                f'<td style="text-align:right;color:#c62828;">{dias_no_hechas}</td>'
                f'<td style="text-align:center;font-size:18px;">{semaforo}</td>'
                f'<td style="text-align:right;"><b>{pct_txt}</b></td></tr>'
            )
            total_dailies += 1
            if pct is not None and pct >= 70:
                cumplido_total += 1
            if priority == "alta":
                total_dailies_alta += 1
                if pct is not None and pct >= 85:
                    cumplido_alta += 1

        # Tabla weekly
        weekly_rows = ""
        cumplido_proyectos = 0
        total_proyectos = 0
        for aid, agg in summary["actividades_semanales"].items():
            avance = agg.get("avance_final", 0)
            priority = agg.get("priority", "media")
            semaforo = _semaforo_emoji(avance, priority)
            prio_badge = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(priority, "")
            notas = agg.get("notas") or "<span style='color:#aaa;'>—</span>"
            weekly_rows += (
                f'<tr><td>{agg["nombre"]} {prio_badge}</td>'
                f'<td style="text-align:right;"><b>{avance:.0f}%</b></td>'
                f'<td style="text-align:center;font-size:18px;">{semaforo}</td>'
                f'<td>{notas}</td></tr>'
            )
            total_proyectos += 1
            if avance >= 70:
                cumplido_proyectos += 1

        # Razones de no hechas
        razones_html = ""
        razones = summary.get("razones_no_hechas") or []
        if razones:
            razones_html = '<h4 style="color:#c62828;margin-top:12px;">❌ Razones de no cumplimiento</h4><ul>'
            for r in razones[:20]:
                fecha_d = r.get("fecha", "")[5:]  # MM-DD
                priority = r.get("priority", "media")
                prio_badge = {"alta": "🔴 ", "media": "", "baja": ""}.get(priority, "")
                razones_html += (
                    f'<li>{prio_badge}<b>{r.get("actividad")}</b> ({fecha_d}): '
                    f'<i>{r.get("razon")}</i></li>'
                )
            if len(razones) > 20:
                razones_html += f'<li style="color:#888;"><i>... y {len(razones) - 20} más</i></li>'
            razones_html += "</ul>"

        # Resumen header del colaborador
        cumpl_pct = (
            (cumplido_total + cumplido_proyectos) / max(1, total_dailies + total_proyectos) * 100
        )
        cumpl_alta_pct = (
            cumplido_alta / max(1, total_dailies_alta) * 100
            if total_dailies_alta > 0 else None
        )

        alta_html = ""
        if cumpl_alta_pct is not None:
            color = "#2e7d32" if cumpl_alta_pct >= 85 else ("#ef6c00" if cumpl_alta_pct >= 60 else "#c62828")
            alta_html = f'<span style="color:{color};font-weight:600;"> · Prioridad alta: {cumpl_alta_pct:.0f}%</span>'

        user_blocks.append(f"""
<h3>👤 {alias.upper()}</h3>
<p>Cumplimiento general del mes: <b>{cumpl_pct:.0f}%</b>{alta_html}</p>

<h4>📅 Actividades diarias</h4>
<table>
<tr><th>Actividad</th><th style="text-align:right;">Total</th>
<th style="text-align:right;">Días</th><th style="text-align:right;">No hechas</th>
<th>Estado</th><th style="text-align:right;">Cumpl.</th></tr>
{daily_rows or '<tr><td colspan="6" style="color:#888;">Sin actividades diarias</td></tr>'}
</table>

<h4>📌 Proyectos semanales</h4>
<table>
<tr><th>Proyecto</th><th style="text-align:right;">Avance final</th><th>Estado</th><th>Notas</th></tr>
{weekly_rows or '<tr><td colspan="4" style="color:#888;">Sin proyectos semanales</td></tr>'}
</table>

{razones_html}
""")

    if not user_blocks:
        body_users = '<p style="color:#888;"><i>No hay datos de actividades de ningún colaborador en este mes.</i></p>'
    else:
        body_users = "\n".join(user_blocks)

    css = """
    body{font-family:'Segoe UI',Arial,sans-serif;color:#2c2c2c;max-width:820px;margin:0;padding:18px;}
    h2{color:#0e7c39;border-bottom:2px solid #0e7c39;padding-bottom:8px;margin-top:0;}
    h3{color:#0e7c39;margin-top:30px;border-top:1px solid #eee;padding-top:14px;}
    h4{color:#444;margin-top:18px;margin-bottom:6px;}
    table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px;}
    th{background:#0e7c39;color:white;text-align:left;padding:8px 10px;}
    td{border-bottom:1px solid #ececec;padding:7px 10px;}
    .footer{font-size:11px;color:#888;margin-top:30px;border-top:1px solid #eee;padding-top:10px;}
    """

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>

<h2>🎯 Recap de actividades — {nombre_mes} {year}</h2>
<p>Hola Daniel y Gabriela,<br>resumen de actividades del equipo del mes
recién cerrado. El semáforo es <b>más estricto para prioridad ALTA</b>
(≥85% verde) que para media/baja (≥70% verde).</p>

{body_users}

<p style="color:#666;font-size:12px;margin-top:24px;">
🟢 Cumple · 🟡 Parcial · 🔴 Por debajo · ⚪ Sin meta o sin datos<br>
🔴 Priority alta · 🟡 Media · ⚪ Baja
</p>

<div class="footer">
Recap mensual de actividades · {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")} EC<br>
Generado automáticamente por el Activities Bot.
</div>

</body></html>"""


def send_activities_recap(year: int | None = None, month: int | None = None) -> dict:
    """Envía el activities recap del mes (default: mes anterior)."""
    if year is None or month is None:
        year, month = _previous_month()
    nombre_mes = MESES_ES[month - 1].capitalize()
    html = _build_activities_recap_html(year, month)
    subject = f"🎯 Recap de actividades del equipo — {nombre_mes} {year}"
    graph_mail.send(
        from_user=RECAP_FROM,
        to=RECAP_TO,
        subject=subject,
        html_body=html,
    )
    return {"ok": True, "to": RECAP_TO, "subject": subject, "period": f"{year}-{month:02d}"}


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) >= 2 else "help"
    if cmd == "sales":
        year, month = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) >= 4 else _previous_month()
        print(_build_sales_recap_html(year, month)[:2000])
    elif cmd == "activities":
        year, month = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) >= 4 else _previous_month()
        print(_build_activities_recap_html(year, month)[:2000])
    elif cmd == "send-sales":
        year, month = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) >= 4 else _previous_month()
        print(send_sales_recap(year, month))
    elif cmd == "send-activities":
        year, month = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) >= 4 else _previous_month()
        print(send_activities_recap(year, month))
    else:
        print("Comandos: sales [YYYY MM] | activities [YYYY MM] | send-sales [YYYY MM] | send-activities [YYYY MM]")
