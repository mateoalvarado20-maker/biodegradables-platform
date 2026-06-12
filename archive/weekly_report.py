"""Reporte semanal de actividades a Daniel los viernes 5 PM.

Modos:
    python weekly_report.py send          # producción: a JEFE con cc Mateo
    python weekly_report.py test          # solo a Mateo
    python weekly_report.py dry           # imprime HTML, no envía
    python weekly_report.py preview --wk 2026-W21  # preview semana pasada

Lee `activity_state.json`. Pinta semáforos:
- Actividades diarias: total semanal vs (meta diaria * 5 días hábiles)
- Actividades semanales: % avance vs 100%

Umbrales (compartidos con daily_report):
- Diarias: >=100% verde, 85-99% amarillo, <85% rojo
- Semanales: >=100% verde, 60-99% amarillo, <60% rojo
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import activity_state
from daily_report import CSS, JEFE, MIO, _kpi, _kpi_row, color_cumpl
from pbi_cloud import send_email

LOCAL_TZ = timezone(timedelta(hours=-5))

DIAS_CORTOS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def _color_avance(avance: float | None) -> str:
    if avance is None:
        return "muted"
    if avance >= 100:
        return "ok"
    if avance >= 60:
        return "warn"
    return "bad"


def _fmt_valor(v: Any, unidad: str = "") -> str:
    if v is None or v == "":
        return "—"
    try:
        f = float(v)
        suf = f" {unidad}" if unidad else ""
        if f == int(f):
            return f"{int(f):,}{suf}"
        return f"{f:,.1f}{suf}"
    except (TypeError, ValueError):
        return str(v)


def _bg_for_cls(cls: str) -> str:
    return {
        "ok": "#e8f5e9",
        "warn": "#fff3e0",
        "bad": "#ffebee",
        "muted": "#f5f5f5",
    }.get(cls, "")


def _row_diaria(a: dict[str, Any], dias_iso: list[str]) -> str:
    log = a.get("log", {})
    cells = []
    total = 0.0
    for d in dias_iso:
        rec = log.get(d)
        if rec:
            try:
                v = float(rec.get("valor", 0) or 0)
                total += v
                cells.append(f'<td class="right">{_fmt_valor(v)}</td>')
            except (TypeError, ValueError):
                cells.append('<td class="right">—</td>')
        else:
            cells.append('<td class="right" style="color:#aaa;">—</td>')

    meta = a.get("meta")
    meta_sem = (meta or 0) * 5
    cumpl = total / meta_sem if meta_sem else None
    cls = color_cumpl(cumpl) if cumpl is not None else ""
    cumpl_txt = f"{cumpl * 100:.0f}%" if cumpl is not None else "—"

    bg = _bg_for_cls(cls)
    style = f' bgcolor="{bg}" style="background-color:{bg};"' if bg else ""

    nombre = a["nombre"]
    if a.get("adhoc"):
        nombre += ' <span style="color:#777;font-size:11px;">(ad-hoc)</span>'

    meta_txt = _fmt_valor(meta_sem) if meta_sem else "—"

    return (
        f'<tr{style}><td>{nombre}</td>'
        + "".join(cells)
        + f'<td class="right"><b>{_fmt_valor(total)}</b></td>'
        + f'<td class="right">{meta_txt}</td>'
        + f'<td class="right"><b>{cumpl_txt}</b></td></tr>'
    )


def _row_semanal(a: dict[str, Any]) -> str:
    avance = a.get("avance") or 0
    cls = _color_avance(avance)
    bg = _bg_for_cls(cls)
    style = f' bgcolor="{bg}" style="background-color:{bg};"' if bg else ""
    nombre = a["nombre"]
    if a.get("adhoc"):
        nombre += ' <span style="color:#777;font-size:11px;">(ad-hoc)</span>'
    notas = a.get("notas") or '<span style="color:#aaa;">—</span>'
    return (
        f'<tr{style}><td>{nombre}</td>'
        f'<td class="right"><b>{avance:.0f}%</b></td>'
        f'<td>{notas}</td></tr>'
    )


def html_weekly(wk: str | None = None) -> str:
    wk = wk or activity_state.week_key()
    data = activity_state.get_week(wk)
    monday, friday = activity_state.week_range(wk)

    dias_iso = [(monday + timedelta(days=i)).isoformat() for i in range(5)]
    dias_labels = [
        f"{DIAS_CORTOS[i]} {(monday + timedelta(days=i)).day}" for i in range(5)
    ]

    diarias = [(aid, a) for aid, a in data["activities"].items() if a["tipo"] == "diaria"]
    semanales = [(aid, a) for aid, a in data["activities"].items() if a["tipo"] != "diaria"]

    # KPIs cabecera
    apollo_correos = next((a for aid, a in diarias if aid == "apollo-correos"), None)
    apollo_resp = next((a for aid, a in diarias if aid == "apollo-respuestas"), None)
    correos_total = activity_state.daily_total(apollo_correos)
    correos_cumpl = activity_state.daily_compliance(apollo_correos)
    resp_total = activity_state.daily_total(apollo_resp)
    avance_promedio = (
        sum((a.get("avance") or 0) for _, a in semanales) / len(semanales)
        if semanales else 0
    )

    correos_cls = color_cumpl(correos_cumpl) if correos_cumpl is not None else "muted"
    avance_cls = _color_avance(avance_promedio)

    diarias_header = (
        '<tr><th>Actividad</th>'
        + "".join(f'<th class="right">{lbl}</th>' for lbl in dias_labels)
        + '<th class="right">Total</th>'
        + '<th class="right">Meta sem.</th>'
        + '<th class="right">Cumpl.</th></tr>'
    )

    diarias_block = ""
    if diarias:
        diarias_rows = "".join(_row_diaria(a, dias_iso) for _, a in diarias)
        diarias_block = f"""
<h3>📅 Actividades diarias</h3>
<table>
{diarias_header}
{diarias_rows}
</table>
<p class="muted-text">Meta semanal = meta diaria × 5 días hábiles. Cumplimiento ≥100% verde, 85-99% amarillo, &lt;85% rojo.</p>"""

    semanales_block = ""
    if semanales:
        semanales_rows = "".join(_row_semanal(a) for _, a in semanales)
        semanales_block = f"""
<h3>📌 Proyectos / Actividades semanales</h3>
<table>
<tr><th>Actividad</th><th class="right">Avance</th><th>Notas</th></tr>
{semanales_rows}
</table>
<p class="muted-text">Avance ≥100% verde, 60-99% amarillo, &lt;60% rojo.</p>"""

    pendientes_block = ""
    pendientes = [a["nombre"] for _, a in semanales if (a.get("avance") or 0) < 60]
    if pendientes:
        items = "".join(f"<li>{n}</li>" for n in pendientes)
        pendientes_block = f"""
<h3>⚠️ Pendientes / a recuperar</h3>
<ul>{items}</ul>"""

    fecha_envio = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<h2>Resumen Semanal de Actividades — Mateo</h2>
<p>Hola Daniel,<br>
te comparto el cierre de la semana <b>{wk}</b>
({monday.strftime('%d/%m')} al {friday.strftime('%d/%m/%Y')}).</p>

<h3>🎯 KPIs de la semana</h3>
{_kpi_row(
    _kpi("Correos Apollo enviados", _fmt_valor(correos_total), correos_cls),
    _kpi("Respuestas recibidas", _fmt_valor(resp_total), "muted"),
    _kpi("Avance proyectos (prom.)", f"{avance_promedio:.0f}%", avance_cls),
)}
{diarias_block}
{semanales_block}
{pendientes_block}

<div class="footer">
Reporte generado automáticamente · {fecha_envio}<br>
Fuente: tracker local de actividades. Datos marcados manualmente por Mateo
durante la semana · próximamente vía bot de Teams.
</div>
</body></html>"""


def main() -> int:
    p = argparse.ArgumentParser(description="Reporte semanal de actividades.")
    p.add_argument("mode", choices=["send", "test", "dry", "preview"])
    p.add_argument("--wk", default=None, help="Semana ISO AAAA-Www (default: actual).")
    args = p.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

    html = html_weekly(args.wk)

    if args.mode in ("dry", "preview"):
        print(html)
        return 0

    wk = args.wk or activity_state.week_key()
    today_str = datetime.now(LOCAL_TZ).strftime("%d/%m/%Y")
    subject = f"Resumen semanal de actividades — {today_str} ({wk})"

    to = MIO if args.mode == "test" else JEFE
    cc = None if args.mode == "test" else MIO

    send_email(to, subject, html, cc=cc)
    cc_txt = f" (cc: {cc})" if cc else ""
    print(f"[OK] Enviado a {to}{cc_txt} · semana {wk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
