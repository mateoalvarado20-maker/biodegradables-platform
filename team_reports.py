"""team_reports — reportes y correos del EQUIPO del tenant (F4.2 VER-IA).

Extraído de ask_agent.py el 2026-07-03: estas ~2.500 líneas de HTML de
correos (resúmenes diarios/semanales, consolidado, cierre/apertura de caja,
bloque del chofer, workload) no son parte del agente conversacional — son la
capa de REPORTES del equipo. ask_agent re-exporta estos nombres por
compatibilidad; el código nuevo debe importar desde team_reports.

Regla de capas: este módulo NO importa ask_agent (la dependencia es
ask_agent → team_reports, nunca al revés).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import activity_state
import contifico_client
import core_config
import graph_mail
import news_brief

LOCAL_TZ = timezone(timedelta(hours=-5))

logger = logging.getLogger("team_reports")

# ===== Identidad/destinatarios del equipo (movidos de ask_agent en F4.2) =====
# Config de A QUIÉN se reporta — pertenece a la capa de reportes. ask_agent
# los re-importa por compatibilidad.
TRACKER_EMAIL_FROM = os.environ.get(
    "TRACKER_TARGET_USER", core_config.MIO
).strip()
TRACKER_EMAIL_TO_DEFAULT = ",".join(core_config.JEFE)

# Mapa email → nombre humano, derivado del directorio del tenant.
EMAIL_TO_NAME = core_config.EMAIL_TO_NAME


def _load_collaborators() -> dict[str, str]:
    """Directorio alias → email. Default = directorio del tenant
    (primer-nombre:email); overridable por env KNOWN_COLLABORATORS."""
    _default = ",".join(
        f"{p['name'].split()[0].lower()}:{e}"
        for e, p in core_config.PEOPLE.items() if p.get("name")
    )
    raw = os.environ.get("KNOWN_COLLABORATORS", _default)
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            alias, email = entry.split(":", 1)
            out[alias.strip().lower()] = email.strip().lower()
    return out


COLLABORATORS = _load_collaborators()

def _hoy_ec() -> date:
    """Fecha de HOY en zona Ecuador. Fase 5 (auditoría A9): date.today()
    usa la TZ del server — en Azure (UTC) entre 19:00 y 23:59 EC ya es
    MAÑANA, y las marcas caían en la fecha equivocada."""
    return datetime.now(LOCAL_TZ).date()


def _ultimo_sabado(hoy: date | None = None) -> date:
    """Sábado más reciente. Si `hoy` es sábado, lo retorna; si no, retrocede
    al sábado anterior. Usado por el recap del lunes (que corre el lunes 8 AM
    y debe reportar el sábado anterior).

      lunes  → sábado (hoy − 2)
      martes → sábado (hoy − 3)
      sábado → ese mismo sábado
    """
    hoy = hoy or _hoy_ec()
    delta = (hoy.weekday() - 5) % 7  # días desde el último sábado
    return hoy - timedelta(days=delta)





def _summary_html(user_email: str | None = None) -> str:
    """Construye el HTML del correo de resumen del día del usuario.

    Phase K+L: incluye sección horario, ordena por priority, marca carry-overs
    en rojo, separa en Hechas/Parciales/No hechas/Sin marcar, agrega comparativo
    semanal y proyectos pendientes.
    """
    hoy = _hoy_ec()
    yesterday = hoy - timedelta(days=1)
    today_iso = hoy.isoformat()
    yesterday_iso = yesterday.isoformat()
    week = activity_state.get_week(user_email)
    horario = activity_state.get_day_schedule(user_email, today_iso)

    # Clasificar daily activities (sorted by priority first)
    diarias_items = [
        (aid, a) for aid, a in week["activities"].items() if a["tipo"] == "diaria"
    ]
    diarias_items = activity_state.sort_activities_by_priority_then_carryover(
        diarias_items, today_iso, yesterday_iso
    )

    hechas: list = []
    parciales: list = []
    no_hechas: list = []
    sin_marcar: list = []
    carry_overs: list = []
    weekly_actualizadas: list = []
    weekly_no_actualizadas: list = []

    for aid, a in diarias_items:
        is_co = activity_state.is_carryover_alta(a, today_iso, yesterday_iso)
        log = a.get("log", {})
        rec = log.get(today_iso)
        if is_co:
            carry_overs.append((a, log.get(yesterday_iso) or {}))
        if rec is None:
            sin_marcar.append(a)
        else:
            valor = rec.get("valor", 0) or 0
            meta = a.get("meta")
            if valor == 0:
                no_hechas.append((a, rec))
            elif meta and valor < meta:
                parciales.append((a, rec))
            else:
                hechas.append((a, rec))

    for aid, a in week["activities"].items():
        if a["tipo"] == "diaria":
            continue
        ult = a.get("ultima_actualizacion") or ""
        if ult.startswith(today_iso):
            weekly_actualizadas.append(a)
        else:
            # Si avance < 100, está en curso y sin update hoy = pendiente de avanzar
            if (a.get("avance") or 0) < 100:
                weekly_no_actualizadas.append(a)

    def _prio_badge(priority: str) -> str:
        emoji = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(priority, "")
        return f' <span style="font-size:11px;">{emoji}</span>' if emoji else ""

    def _row(a, rec, show_valor=True):
        nombre = a["nombre"] + _prio_badge(a.get("priority", "media"))
        meta = a.get("meta")
        valor = rec.get("valor")
        notas = (rec.get("notas") or "").strip()
        detalle_partes = []
        if show_valor and valor is not None:
            txt = f"{valor:g}"
            if meta:
                txt += f" / {meta}"
            detalle_partes.append(txt)
        if notas:
            detalle_partes.append(f'<i>"{notas}"</i>')
        detalle = " — ".join(detalle_partes) or "—"
        return f"<tr><td>{nombre}</td><td>{detalle}</td></tr>"

    def _section_table(items, color):
        if not items:
            return ""
        rows = "".join(_row(a, rec) for a, rec in items)
        return (
            f'<table><tr><th>Actividad</th><th>Detalle</th></tr>{rows}</table>'
        )

    weekly_rows = ""
    for a in weekly_actualizadas:
        notas = a.get("notas") or "<span style='color:#aaa;'>—</span>"
        weekly_rows += (
            f'<tr><td>{a["nombre"]}{_prio_badge(a.get("priority", "media"))}</td>'
            f'<td style="text-align:right;"><b>{a.get("avance", 0):.0f}%</b></td>'
            f'<td>{notas}</td></tr>'
        )

    weekly_pending_rows = ""
    for a in weekly_no_actualizadas:
        avance = a.get("avance") or 0
        ult = a.get("ultima_actualizacion") or ""
        ult_corto = ult[:10] if ult else "nunca"
        bg = "#fff3e0" if avance < 50 else "#ffffff"
        weekly_pending_rows += (
            f'<tr bgcolor="{bg}" style="background:{bg};">'
            f'<td>{a["nombre"]}{_prio_badge(a.get("priority", "media"))}</td>'
            f'<td style="text-align:right;">{avance:.0f}%</td>'
            f'<td style="font-size:12px;color:#666;">{ult_corto}</td></tr>'
        )

    # Carry-over section (top, red)
    carryover_section = ""
    if carry_overs:
        rows = ""
        for a, rec_ayer in carry_overs:
            razon_ayer = (rec_ayer.get("notas") or "").strip()
            ayer_txt = (
                f'<span style="color:#c62828;"><i>"{razon_ayer}"</i></span>'
                if razon_ayer
                else '<span style="color:#999;">No se marcó ayer</span>'
            )
            rows += (
                f'<tr style="background:#ffebee;">'
                f'<td style="text-decoration:underline;color:#c62828;font-weight:600;">'
                f'{a["nombre"]} 🔴</td>'
                f'<td>{ayer_txt}</td></tr>'
            )
        carryover_section = (
            '<h3 style="color:#c62828;">🚨 Pendientes importantes (no se hicieron ayer)</h3>'
            f'<table><tr><th>Actividad</th><th>Razón / Estado de ayer</th></tr>{rows}</table>'
            '<p style="color:#666;font-size:12px;">Estas son las marcadas de prioridad <b>ALTA</b> que quedaron sin hacer.</p>'
        )

    # Weekly comparison (this week vs last week, same days so far)
    weekly_comparison_html = _build_weekly_comparison(user_email, hoy)

    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    fecha_humana = f"{dias_es[hoy.weekday()]} {hoy.day} de {meses_es[hoy.month-1]} de {hoy.year}"

    # Horario section
    if horario is None:
        horario_html = (
            '<p style="color:#999;font-style:italic;">Horario: sin reportar.</p>'
        )
    elif horario.get("estandar"):
        horario_html = (
            f'<p>⏰ Horario: <b>estándar ({activity_state.horario_estandar_label(hoy)})</b>.</p>'
        )
    else:
        desde = horario.get("desde") or "?"
        hasta = horario.get("hasta") or "?"
        razon = horario.get("razon") or "Sin razón especificada"
        horario_html = (
            f'<p>⏰ Horario: <b>{desde} – {hasta}</b> '
            f'<span style="color:#777;">(no estándar)</span><br>'
            f'<span style="color:#555;">Razón: <i>{razon}</i></span></p>'
        )

    # Determinar quién es el colaborador (slug del email)
    if user_email and "@" in user_email:
        alias = user_email.split("@")[0]
    else:
        alias = "colaborador"

    css = """
    body{font-family:'Segoe UI',Arial,sans-serif;color:#2c2c2c;max-width:720px;
         margin:0;padding:18px;}
    h2{color:#0e7c39;border-bottom:2px solid #0e7c39;padding-bottom:8px;margin-top:0;}
    h3{margin-top:24px;margin-bottom:8px;}
    h3.ok{color:#2e7d32;}
    h3.warn{color:#ef6c00;}
    h3.bad{color:#c62828;}
    h3.muted{color:#888;}
    h3.weekly{color:#0e7c39;}
    table{border-collapse:collapse;width:100%;margin-top:6px;font-size:13px;}
    th{background:#0e7c39;color:white;text-align:left;padding:8px 10px;font-weight:600;}
    td{border-bottom:1px solid #ececec;padding:8px 10px;}
    .footer{font-size:11px;color:#888;margin-top:30px;border-top:1px solid #eee;padding-top:10px;}
    """

    section_hechas = (
        f'<h3 class="ok">✅ Hechas ({len(hechas)})</h3>{_section_table(hechas, "ok")}'
        if hechas else ""
    )
    section_parciales = (
        f'<h3 class="warn">⚠️ Parciales ({len(parciales)})</h3>{_section_table(parciales, "warn")}'
        if parciales else ""
    )
    section_no_hechas = (
        f'<h3 class="bad">❌ No hechas ({len(no_hechas)})</h3>{_section_table(no_hechas, "bad")}'
        if no_hechas else ""
    )
    section_sin_marcar = ""
    if sin_marcar:
        rows = "".join(f"<tr><td>{a['nombre']}</td></tr>" for a in sin_marcar)
        section_sin_marcar = (
            f'<h3 class="muted">⏳ Sin marcar ({len(sin_marcar)})</h3>'
            f'<table><tr><th>Actividad</th></tr>{rows}</table>'
        )

    weekly_section = ""
    if weekly_rows:
        weekly_section = (
            f'<h3 class="weekly">📌 Proyectos semanales actualizados hoy</h3>'
            f'<table><tr><th>Proyecto</th><th style="text-align:right;">Avance</th><th>Notas</th></tr>'
            f'{weekly_rows}</table>'
        )

    weekly_pending_section = ""
    if weekly_pending_rows:
        weekly_pending_section = (
            f'<h3 style="color:#ef6c00;">📋 Proyectos en curso sin avance hoy</h3>'
            f'<table><tr><th>Proyecto</th><th style="text-align:right;">Avance actual</th><th style="font-size:12px;">Última actualización</th></tr>'
            f'{weekly_pending_rows}</table>'
            f'<p style="color:#666;font-size:12px;">Para no perderlos de vista. Pintados ámbar los que están por debajo del 50%.</p>'
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>
<h2>Resumen de actividades — {alias}</h2>
<p>Hola,<br>
acá el cierre del día de hoy:<br>
<span style="color:#777;font-size:12px;">{fecha_humana}</span></p>

{horario_html}

{carryover_section}

{section_hechas}
{section_parciales}
{section_no_hechas}
{section_sin_marcar}

{weekly_section}

{weekly_pending_section}

{weekly_comparison_html}

<div class="footer">
Resumen enviado automáticamente por el Activities Bot · {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")} (hora Ecuador)<br>
🔴 Alta prioridad · 🟡 Media · ⚪ Baja
</div>
</body></html>"""


def _build_weekly_comparison(user_email: str | None, hoy: date) -> str:
    """Bloque mini de comparación semanal: actividades diarias totales esta
    semana (Lun→hoy) vs misma cantidad de días la semana pasada."""
    monday = hoy - timedelta(days=hoy.weekday())
    prev_monday = monday - timedelta(weeks=1)
    days_so_far = (hoy - monday).days + 1  # incl. today

    state = activity_state.load()
    email = (user_email or activity_state.DEFAULT_USER).strip().lower()
    user_state = state.get("users", {}).get(email, {})
    weeks = user_state.get("weeks", {})

    wk_now_key = activity_state.week_key(hoy)
    prev_iso = prev_monday.isocalendar()
    wk_prev_key = f"{prev_iso[0]:04d}-W{prev_iso[1]:02d}"
    wk_now = weeks.get(wk_now_key, {})
    wk_prev = weeks.get(wk_prev_key)
    if not wk_prev:
        return ""

    def _sum_daily_for_range(wk_data, days):
        per_activity: dict[str, float] = {}
        for aid, a in wk_data.get("activities", {}).items():
            if a.get("tipo") != "diaria":
                continue
            total = 0.0
            for d_iso, rec in (a.get("log") or {}).items():
                try:
                    d = date.fromisoformat(d_iso)
                except ValueError:
                    continue
                # Sum solo si está dentro del rango días
                mon = wk_data.get("lunes")
                if mon:
                    try:
                        mon_date = date.fromisoformat(mon)
                        days_into_wk = (d - mon_date).days
                        if 0 <= days_into_wk < days:
                            total += float(rec.get("valor") or 0)
                    except ValueError:
                        continue
            per_activity[aid] = (total, a.get("nombre", aid))
        return per_activity

    now_data = _sum_daily_for_range(wk_now, days_so_far)
    prev_data = _sum_daily_for_range(wk_prev, days_so_far)

    rows = ""
    has_any = False
    for aid, (total_now, nombre) in now_data.items():
        total_prev = prev_data.get(aid, (0.0, nombre))[0]
        if total_now == 0 and total_prev == 0:
            continue
        has_any = True
        if total_prev > 0:
            delta_pct = (total_now - total_prev) / total_prev * 100
            if delta_pct > 5:
                delta_html = f'<span style="color:#2e7d32;">▲ +{delta_pct:.0f}%</span>'
            elif delta_pct < -5:
                delta_html = f'<span style="color:#c62828;">▼ {delta_pct:.0f}%</span>'
            else:
                delta_html = '<span style="color:#888;">≈ similar</span>'
        else:
            delta_html = '<span style="color:#2e7d32;">▲ nuevo</span>'
        rows += (
            f'<tr><td>{nombre}</td>'
            f'<td style="text-align:right;">{total_now:.0f}</td>'
            f'<td style="text-align:right;color:#888;">{total_prev:.0f}</td>'
            f'<td style="text-align:right;">{delta_html}</td></tr>'
        )

    if not has_any:
        return ""

    return (
        f'<h3 style="color:#0e7c39;">📊 Esta semana vs la pasada '
        f'(primeros {days_so_far} días)</h3>'
        f'<table><tr><th>Actividad</th>'
        f'<th style="text-align:right;">Esta sem.</th>'
        f'<th style="text-align:right;">Sem. ant.</th>'
        f'<th style="text-align:right;">Delta</th></tr>{rows}</table>'
    )


def _resolve_supervisors(user_email: str) -> list[str]:
    """Devuelve la lista de supervisores del usuario.

    Prioridad de lookup:
    1. env var por alias: TRACKER_EMAIL_TO_<ALIAS> (ej. TRACKER_EMAIL_TO_MATEO)
    2. env var global: TRACKER_EMAIL_TO
    3. default hardcoded: Daniel + Gabriela

    Esto permite que cada colaborador reporte a su propio supervisor
    (ej. Gabriela reporta solo a Daniel; Mateo reporta a Daniel + Gabriela).
    """
    email_lower = (user_email or "").lower()
    # Buscar el alias del usuario en el directorio. Resolución EN CALIENTE
    # (F4.2): leerlo al momento del uso evita valores congelados al importar
    # (env/config cambiados aplican sin restart y sin sensibilidad al orden
    # de carga de módulos en tests).
    for alias, mapped_email in _load_collaborators().items():
        if mapped_email == email_lower:
            override = os.environ.get(f"TRACKER_EMAIL_TO_{alias.upper()}")
            if override:
                return [e.strip() for e in override.split(",") if e.strip()]
            break
    # Fallback al global
    global_to = os.environ.get("TRACKER_EMAIL_TO", ",".join(core_config.JEFE))
    return [e.strip() for e in global_to.split(",") if e.strip()]


def _weekly_summary_html(user_email: str | None = None) -> str:
    """Construye HTML del resumen semanal del colaborador.

    Estructura:
    - KPIs cabecera (Apollo, cobranzas, avance proyectos)
    - Tabla actividades diarias con columna por día + cumplimiento semanal
    - Tabla proyectos semanales con avance
    - Cobranzas: contactadas vs pendientes
    - Comparativo vs semana anterior (si hay data)
    """
    today = _hoy_ec()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    wk_key = activity_state.week_key()
    prev_monday = monday - timedelta(weeks=1)
    prev_iso = prev_monday.isocalendar()
    prev_wk_key = f"{prev_iso[0]:04d}-W{prev_iso[1]:02d}"

    week = activity_state.get_week(user_email, wk=wk_key)
    # Cargar semana anterior sin crear si no existe
    state = activity_state.load()
    user_slug = (user_email or activity_state.DEFAULT_USER).strip().lower()
    prev_week = state.get("users", {}).get(user_slug, {}).get("weeks", {}).get(prev_wk_key)

    diarias = [(aid, a) for aid, a in week["activities"].items() if a["tipo"] == "diaria"]
    semanales = [(aid, a) for aid, a in week["activities"].items() if a["tipo"] != "diaria"]

    dias_es_corto = ["L", "M", "M", "J", "V", "S", "D"]
    dias_iso = [(monday + timedelta(days=i)).isoformat() for i in range(6)]  # Lun-Sab

    # Tabla daily con columna por día
    daily_rows = ""
    for aid, a in diarias:
        log = a.get("log", {})
        cells = ""
        total = 0.0
        for d in dias_iso:
            rec = log.get(d)
            if rec:
                try:
                    v = float(rec.get("valor", 0) or 0)
                    total += v
                    cells += f'<td style="text-align:right;">{v:.0f}</td>'
                except (TypeError, ValueError):
                    cells += '<td style="text-align:right;">—</td>'
            else:
                cells += '<td style="text-align:right;color:#aaa;">—</td>'
        meta = a.get("meta")
        meta_sem = (meta or 0) * 5
        cumpl = (total / meta_sem) if meta_sem else None
        cumpl_txt = f"{cumpl * 100:.0f}%" if cumpl is not None else "—"
        bg = ""
        if cumpl is not None:
            if cumpl >= 1.0: bg = "background:#e8f5e9;"
            elif cumpl >= 0.85: bg = "background:#fff3e0;"
            else: bg = "background:#ffebee;"
        daily_rows += (
            f'<tr style="{bg}"><td>{a["nombre"]}</td>{cells}'
            f'<td style="text-align:right;"><b>{total:.0f}</b></td>'
            f'<td style="text-align:right;">{meta_sem:.0f if meta_sem else 0}</td>'
            f'<td style="text-align:right;"><b>{cumpl_txt}</b></td></tr>'
        )

    # Tabla semanal con avance
    weekly_rows = ""
    for aid, a in semanales:
        avance = a.get("avance") or 0
        # Buscar avance previo de la semana anterior
        prev_avance = None
        if prev_week:
            prev_a = prev_week.get("activities", {}).get(aid)
            if prev_a and prev_a.get("tipo") != "diaria":
                prev_avance = prev_a.get("avance", 0)
        delta = ""
        if prev_avance is not None:
            d = avance - prev_avance
            if d > 0:
                delta = f' <span style="color:#0e7c39;">(+{d:.0f}%)</span>'
            elif d < 0:
                delta = f' <span style="color:#c62828;">({d:.0f}%)</span>'
            else:
                delta = ' <span style="color:#888;">(sin cambio)</span>'
        notas = a.get("notas") or '—'
        bg = ""
        if avance >= 100: bg = "background:#e8f5e9;"
        elif avance >= 60: bg = "background:#fff3e0;"
        else: bg = "background:#ffebee;"
        weekly_rows += (
            f'<tr style="{bg}"><td>{a["nombre"]}</td>'
            f'<td style="text-align:right;"><b>{avance:.0f}%</b>{delta}</td>'
            f'<td>{notas}</td></tr>'
        )

    # Cobranzas (filtrar activities con nombre que empieza con "📞 Cobranza")
    cobranzas = []
    for aid, a in week["activities"].items():
        if a["tipo"] == "diaria":
            continue
        nombre = a.get("nombre", "")
        if "Cobranza" in nombre or aid.startswith("cobranza"):
            cobranzas.append(a)
    cobranzas_contactadas = sum(1 for c in cobranzas if (c.get("avance") or 0) >= 100)
    cobranzas_total = len(cobranzas)
    cobranza_pct = (cobranzas_contactadas / cobranzas_total * 100) if cobranzas_total else None

    css = """
    body{font-family:'Segoe UI',Arial,sans-serif;color:#2c2c2c;max-width:780px;margin:0;padding:18px;}
    h2{color:#0e7c39;border-bottom:2px solid #0e7c39;padding-bottom:8px;margin-top:0;}
    h3{color:#0e7c39;margin-top:26px;margin-bottom:8px;}
    table{border-collapse:collapse;width:100%;margin-top:6px;font-size:12.5px;}
    th{background:#0e7c39;color:white;text-align:left;padding:7px 10px;}
    td{border-bottom:1px solid #ececec;padding:7px 10px;}
    .muted{color:#777;font-size:12px;}
    .footer{font-size:11px;color:#888;margin-top:30px;border-top:1px solid #eee;padding-top:10px;}
    """

    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    rango = f"{monday.day}/{monday.month} al {friday.day}/{friday.month} de {monday.year}"

    daily_header = (
        '<tr><th>Actividad</th>'
        + ''.join(f'<th style="text-align:right;">{d}</th>' for d in dias_es_corto[:6])
        + '<th style="text-align:right;">Total</th>'
        + '<th style="text-align:right;">Meta sem.</th>'
        + '<th style="text-align:right;">Cumpl.</th></tr>'
    )

    daily_block = ""
    if diarias:
        daily_block = f"""
<h3>📅 Actividades diarias</h3>
<table>{daily_header}{daily_rows}</table>
<p class="muted">Cumplimiento &ge;100% verde, 85-99% amarillo, &lt;85% rojo.</p>"""

    weekly_block = ""
    if semanales:
        weekly_block = f"""
<h3>📌 Proyectos / actividades semanales</h3>
<table><tr><th>Actividad</th><th style="text-align:right;">Avance</th><th>Notas</th></tr>{weekly_rows}</table>
<p class="muted">Delta vs semana anterior si aplica.</p>"""

    cobranza_block = ""
    if cobranzas_total > 0:
        cobranza_block = f"""
<h3>📞 Cobranzas de la semana</h3>
<p>Asignadas: <b>{cobranzas_total}</b>. Contactadas: <b>{cobranzas_contactadas}</b> ({cobranza_pct:.0f}%).</p>
<p class="muted">Detalle por cliente en el activity tracker del colaborador.</p>"""

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{css}</style></head><body>
<h2>Resumen semanal — {user_slug.split('@')[0]}</h2>
<p>Hola,<br>
acá el cierre de la semana <b>{wk_key}</b> ({rango}):</p>
{daily_block}
{weekly_block}
{cobranza_block}
<div class="footer">
Resumen semanal automático generado por el Activities Bot · {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")} EC
</div>
</body></html>"""


def _send_weekly_summary_email(user_email: str | None = None) -> dict:
    """Envía el resumen semanal del colaborador a sus supervisores."""
    candidate = (user_email or "").strip().lower()
    sender = candidate if "@" in candidate else TRACKER_EMAIL_FROM
    html = _weekly_summary_html(sender)
    to_list = _resolve_supervisors(sender)
    today_str = _hoy_ec().strftime("%d/%m/%Y")
    alias = sender.split("@")[0]
    subject = f"Resumen semanal — {alias} ({today_str})"
    graph_mail.send(
        from_user=sender,
        to=to_list,
        subject=subject,
        html_body=html,
    )
    return {"ok": True, "from": sender, "to": to_list, "subject": subject}


def _send_daily_summary_email(user_email: str | None = None) -> dict:
    """Construye y envía el correo de resumen del día del usuario a sus supervisores.

    From: el propio user (queda en su Sent items).
    To: lookup per-user via _resolve_supervisors (ej. Mateo → Daniel+Gabriela,
        Gabriela → solo Daniel).

    Defensa: si user_email no es un email válido (sin @), usa TRACKER_EMAIL_FROM
    para evitar ErrorInvalidUser de Graph.
    """
    candidate = (user_email or "").strip().lower()
    sender = candidate if "@" in candidate else TRACKER_EMAIL_FROM
    html = _summary_html(user_email)
    to_list = _resolve_supervisors(sender)
    today_str = _hoy_ec().strftime("%d/%m/%Y")
    subject = f"Resumen de actividades — {today_str}"
    graph_mail.send(
        from_user=sender,
        to=to_list,
        subject=subject,
        html_body=html,
    )
    return {"ok": True, "from": sender, "to": to_list, "subject": subject}


# ============ Cierre de caja (Phase N, 2026-06-02) ============
CIERRE_CAJA_TO_DEFAULT = ",".join(core_config.JEFE)
CIERRE_CAJA_CC_DEFAULT = core_config.MIO


def _cierre_caja_html(
    user_email: str, fecha: str, sucursal: str, rec: dict
) -> str:
    """Construye el HTML del correo de cierre de caja del día."""
    fecha_obj = datetime.strptime(fecha, "%Y-%m-%d").date()
    fecha_str = fecha_obj.strftime("%d/%m/%Y")
    responsable = user_email

    def _usd(v: float) -> str:
        return f"${v:,.2f}"

    detalle_billetes = rec.get("detalle_billetes")
    detalle_monedas = rec.get("detalle_monedas")
    if not detalle_billetes or not detalle_monedas:
        # rec puede venir solo con denoms — recalcular para tener detalle
        calc = activity_state.calcular_cierre_caja(rec.get("denoms", {}))
        detalle_billetes = calc["detalle_billetes"]
        detalle_monedas = calc["detalle_monedas"]

    def _row(d: dict) -> str:
        return (
            f"<tr>"
            f"<td style='padding:5px 10px;border-bottom:1px solid #eee;'>"
            f"{d['label']} × {d['cantidad']}</td>"
            f"<td style='padding:5px 10px;border-bottom:1px solid #eee;text-align:right;"
            f"font-family:Consolas,monospace;'>{_usd(d['subtotal'])}</td>"
            f"</tr>"
        )

    billetes_rows = "".join(_row(d) for d in detalle_billetes)
    monedas_rows = "".join(_row(d) for d in detalle_monedas)

    horario = activity_state.get_day_schedule(user_email, fecha)
    if horario and horario.get("estandar"):
        horario_str = f"{activity_state.horario_estandar_label(fecha)} (estándar)"
    elif horario:
        horario_str = (
            f"{horario.get('desde','—')} – {horario.get('hasta','—')}"
            + (f" ({horario.get('razon')})" if horario.get("razon") else "")
        )
    else:
        horario_str = "(no marcado)"

    notas_html = (
        f"<p style='font-size:13px;'><b>Notas:</b> {rec.get('notas') or '<i>(sin notas)</i>'}</p>"
    )

    hora_cierre = (rec.get("marcado_at") or "").split("T")[-1][:5] if rec.get("marcado_at") else "—"

    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><style>
body {{ font-family:'Segoe UI',Arial,sans-serif; color:#2c2c2c; max-width:680px;
       margin:0; padding:18px; }}
h3 {{ color:#0e7c39; margin-top:0; border-bottom:2px solid #0e7c39; padding-bottom:8px; }}
table {{ border-collapse:collapse; font-size:13px; width:100%; }}
.totals {{ background:#f4faf6; border:2px solid #0e7c39; border-radius:6px;
           padding:14px 18px; margin:14px 0; }}
.totals .row {{ display:flex; justify-content:space-between; padding:4px 0; }}
.totals .big {{ font-size:18px; color:#0e7c39; padding-top:8px;
                border-top:2px solid #0e7c39; margin-top:6px; font-weight:600; }}
.footer {{ font-size:11px; color:#888; margin-top:24px; border-top:1px solid #eee;
            padding-top:8px; }}
</style></head><body>

<h3>💵 Cierre de caja {sucursal} – {fecha_str}</h3>

<p>
  <b>Sucursal:</b> {sucursal}<br>
  <b>Responsable:</b> {responsable}<br>
  <b>Hora del cierre:</b> {hora_cierre}<br>
  <b>Horario hoy:</b> {horario_str}
</p>

<table>
  <thead>
    <tr>
      <th style='background:#0e7c39;color:white;padding:8px 10px;text-align:left;'>Conteo</th>
      <th style='background:#0e7c39;color:white;padding:8px 10px;text-align:right;'>Valor</th>
    </tr>
  </thead>
  <tbody>
    <tr><td colspan='2' style='background:#f4faf6;font-weight:600;color:#0e7c39;
        padding:6px 10px;'>Billetes</td></tr>
    {billetes_rows}
    <tr>
      <td style='padding:5px 10px;font-weight:600;background:#fafafa;'>Subtotal billetes</td>
      <td style='padding:5px 10px;text-align:right;font-weight:700;background:#fafafa;
          font-family:Consolas,monospace;'>{_usd(rec['total_billetes'])}</td>
    </tr>
    <tr><td colspan='2' style='background:#f4faf6;font-weight:600;color:#0e7c39;
        padding:6px 10px;'>Monedas</td></tr>
    {monedas_rows}
    <tr>
      <td style='padding:5px 10px;font-weight:600;background:#fafafa;'>Subtotal monedas</td>
      <td style='padding:5px 10px;text-align:right;font-weight:700;background:#fafafa;
          font-family:Consolas,monospace;'>{_usd(rec['total_monedas'])}</td>
    </tr>
  </tbody>
</table>

<div class='totals'>
  <div class='row'><span style='color:#555;'>Total en caja</span>
    <span style='font-family:Consolas,monospace;font-weight:600;'>{_usd(rec['total'])}</span></div>
  <div class='row' style='color:#c62828;'><span>(–) Fondo de caja</span>
    <span style='font-family:Consolas,monospace;'>– {_usd(rec['fondo'])}</span></div>
  <div class='row big'><span>VALOR RESTANTE ENTREGADO</span>
    <span style='font-family:Consolas,monospace;'>{_usd(rec['entregado'])}</span></div>
</div>

{notas_html}

<div class='footer'>
  Cierre marcado automáticamente por el Activities Bot el {fecha_str} a las {hora_cierre}.
  El fondo de caja de {_usd(rec['fondo'])} se mantiene en caja para apertura del día siguiente.
</div>

</body></html>
"""
    return html


def _collaborator_block_html_v2(user_email: str, target_date: date | None = None) -> str:
    """Phase T (2026-06-09): bloque por colaborador en el correo consolidado.

    Diseño nuevo: caja grande con header colorido (alias), horario destacado,
    actividades por tipo, y para sucursales: cierre de caja COMPLETO con
    denominaciones (reemplaza el correo separado de cierre de caja).

    `target_date` (2026-06-15): si se pasa, el bloque se arma para ESA fecha
    (semana ISO incluida) en vez de hoy — lo usa el recap del sábado que corre
    el lunes. Si es None, se comporta igual que siempre (hoy EC).
    """
    hoy = target_date or _hoy_ec()
    yesterday = hoy - timedelta(days=1)
    today_iso = hoy.isoformat()
    yesterday_iso = yesterday.isoformat()
    week = activity_state.get_week(user_email, wk=activity_state.week_key(hoy))
    horario = activity_state.get_day_schedule(user_email, today_iso)
    alias = user_email.split("@")[0] if "@" in user_email else user_email
    es_sabado_recap = target_date is not None and hoy.weekday() == 5

    # Determinar role + título del bloque (desde core_config.PEOPLE, sin hardcode)
    es_asistente = user_email.lower() in ASISTENTE_EMAILS
    _suc_name = core_config.sucursal_name_for(user_email)
    _person_name = core_config.display_name_for(user_email)
    if es_asistente:
        _num = core_config.PEOPLE.get(user_email.lower(), {}).get("asistente_num")
        _label = f"ASISTENTE {_num}" if _num else "ASISTENTE"
        titulo_bloque = (
            f"📦 {_label} — {_suc_name.upper()}" if _suc_name else f"📦 {_label}"
        )
        sucursal = _suc_name
    elif _person_name:
        titulo_bloque = f"👤 {_person_name.upper()}"
        sucursal = ""
    else:
        titulo_bloque = f"👤 {alias.upper()}"
        sucursal = ""

    # Rotación GYE de sábados: si el Asistente 1 GYE no reportó nada el sábado,
    # se interpreta como ausencia esperada por turno (no pendiente/error).
    if (es_sabado_recap
            and user_email.lower() == GYE_ASISTENTE1_EMAIL
            and _gye_sin_reporte_dia(user_email, today_iso)):
        return _ausencia_rotativa_block_html(titulo_bloque, hoy.strftime("%d/%m/%Y"))

    # === Horario destacado ===
    if horario is None:
        horario_html = (
            '<span style="color:#999;font-style:italic;">⏰ Horario: sin reportar</span>'
        )
    elif horario.get("estandar"):
        horario_html = (
            '<span style="color:#2e7d32;font-weight:600;">'
            f'⏰ Horario: {activity_state.horario_estandar_label(today_iso)} (estándar) ✅</span>'
        )
    else:
        desde = horario.get("desde") or "?"
        hasta = horario.get("hasta") or "?"
        razon = horario.get("razon") or "sin razón especificada"
        horario_html = (
            f'<span style="color:#ef6c00;font-weight:600;">'
            f'⏰ Horario: {desde} – {hasta} (no estándar)</span><br>'
            f'<span style="color:#666;font-size:12px;">Razón: {razon}</span>'
        )

    # === Daily activities ===
    # 2026-07-04: las COBRANZAS se separan en su propia sección (abajo) — son
    # trabajo de otra naturaleza y a gerencia le llegaba todo mezclado en la
    # misma tabla.
    todas_diarias = [
        (aid, a) for aid, a in week["activities"].items() if a["tipo"] == "diaria"
    ]
    todas_diarias = activity_state.sort_activities_by_priority_then_carryover(
        todas_diarias, today_iso, yesterday_iso
    )
    cobranza_items = [
        (aid, a) for aid, a in todas_diarias if aid.startswith("cobranza-")
    ]
    diarias_items = [
        (aid, a) for aid, a in todas_diarias if not aid.startswith("cobranza-")
    ]
    # Dedup cobranzas por cliente (2026-06-25): auto_assign creaba un aid
    # `cobranza-<cliente>-<fecha>` por día y el MISMO cliente se repetía como
    # varias filas en la tabla. Elegimos UNA activity por cliente — la marcada
    # hoy si existe; las demás se omiten del render.
    _chosen_cob: dict = {}  # cliente -> (aid, marcada_hoy)
    for _aid, _a in cobranza_items:
        _nom = _a.get("nombre", "").replace("📞 Cobranza:", "").strip()
        _cli = _nom.split(" — ")[0].strip() if " — " in _nom else _nom
        _marcada = (_a.get("log") or {}).get(today_iso) is not None
        _cur = _chosen_cob.get(_cli)
        if _cur is None or (_marcada and not _cur[1]):
            _chosen_cob[_cli] = (_aid, _marcada)
    _cob_keep = {v[0] for v in _chosen_cob.values()}

    daily_rows = ""
    counts = {"hecha": 0, "parcial": 0, "no": 0, "sin": 0}
    for aid, a in diarias_items:
        rec = (a.get("log") or {}).get(today_iso)
        meta = a.get("meta")
        if rec is None:
            counts["sin"] += 1
            icon, label, color = "⏳", "Sin marcar", "#999"
            detalle = "—"
        else:
            valor = rec.get("valor", 0) or 0
            notas = (rec.get("notas") or "").strip()
            if valor == 0:
                counts["no"] += 1
                icon, label, color = "❌", "No hecha", "#c62828"
            elif meta and valor < meta:
                counts["parcial"] += 1
                icon, label, color = "⚠️", "Parcial", "#ef6c00"
            else:
                counts["hecha"] += 1
                icon, label, color = "✅", "Hecha", "#2e7d32"
            detalle = ""
            if valor is not None and valor != 0:
                detalle = f"{valor:g}"
                if meta:
                    detalle += f"/{meta}"
            if notas:
                detalle += (' · ' if detalle else '') + f'<i>"{notas}"</i>'
            detalle = detalle or "—"
        # Phase V (2026-06-11): meta semanal opcional para activities diarias
        # (ej. video-tiktok meta=1/día pero meta_semanal=5/semana).
        # Muestra "X/Y semana" sumando todas las marcas de la semana.
        meta_semanal = a.get("meta_semanal")
        if meta_semanal:
            total_semana = 0
            for log_entry in (a.get("log") or {}).values():
                total_semana += float(log_entry.get("valor") or 0)
            sem_color = "#2e7d32" if total_semana >= meta_semanal else "#ef6c00"
            sem_html = (
                f' · <b style="color:{sem_color};">'
                f'{total_semana:g}/{meta_semanal} semana</b>'
            )
            if detalle == "—":
                detalle = sem_html.lstrip(" ·").strip()
            else:
                detalle += sem_html
        priority = a.get("priority", "media")
        prio = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(priority, "")
        nombre = a["nombre"]
        daily_rows += (
            f'<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;">{prio} {nombre}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;color:{color};'
            f'font-weight:600;white-space:nowrap;">{icon} {label}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555;">{detalle}</td>'
            f'</tr>'
        )

    daily_section = ""
    if daily_rows:
        summary_chips = (
            f'<span style="color:#2e7d32;">✅ {counts["hecha"]}</span> · '
            f'<span style="color:#ef6c00;">⚠️ {counts["parcial"]}</span> · '
            f'<span style="color:#c62828;">❌ {counts["no"]}</span> · '
            f'<span style="color:#999;">⏳ {counts["sin"]}</span>'
        )
        daily_section = (
            f'<h4 style="color:#0e7c39;margin:14px 0 4px 0;">📅 Actividades del día '
            f'<span style="font-weight:400;font-size:13px;">({summary_chips})</span></h4>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;'
            f'background:#fff;border:1px solid #d9e0d9;border-radius:4px;">'
            f'<tr style="background:#f4faf6;">'
            f'<th style="text-align:left;padding:5px 8px;color:#0e7c39;">Actividad</th>'
            f'<th style="text-align:left;padding:5px 8px;color:#0e7c39;">Estado</th>'
            f'<th style="text-align:left;padding:5px 8px;color:#0e7c39;">Detalle</th>'
            f'</tr>{daily_rows}</table>'
        )

    # === Cobranzas (sección propia, 2026-07-04) ===
    cobranza_rows = ""
    cob_counts = {"contactada": 0, "no": 0, "sin": 0}
    for aid, a in cobranza_items:
        if aid not in _cob_keep:
            continue  # duplicada del mismo cliente (dedup arriba)
        rec = (a.get("log") or {}).get(today_iso)
        if rec is None:
            cob_counts["sin"] += 1
            icon, label, color = "⏳", "Sin gestionar", "#999"
            obs = "—"
        else:
            valor = rec.get("valor", 0) or 0
            notas = (rec.get("notas") or "").strip()
            if valor > 0:
                cob_counts["contactada"] += 1
                icon, label, color = "📞", "Contactado", "#2e7d32"
            else:
                cob_counts["no"] += 1
                icon, label, color = "❌", "No contactado", "#c62828"
            obs = f'<i>"{notas}"</i>' if notas else "—"
        nombre = a["nombre"].replace("📞 Cobranza:", "").strip()
        cobranza_rows += (
            f'<tr>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;">{nombre}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;color:{color};'
            f'font-weight:600;white-space:nowrap;">{icon} {label}</td>'
            f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555;">{obs}</td>'
            f'</tr>'
        )

    cobranza_section = ""
    if cobranza_rows:
        cob_chips = (
            f'<span style="color:#2e7d32;">📞 {cob_counts["contactada"]}</span> · '
            f'<span style="color:#c62828;">❌ {cob_counts["no"]}</span> · '
            f'<span style="color:#999;">⏳ {cob_counts["sin"]}</span>'
        )
        cobranza_section = (
            f'<h4 style="color:#b26a00;margin:14px 0 4px 0;">📞 Cobranzas '
            f'<span style="font-weight:400;font-size:13px;">({cob_chips})</span></h4>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;'
            f'background:#fff;border:1px solid #ecd9b8;border-radius:4px;">'
            f'<tr style="background:#fdf6ea;">'
            f'<th style="text-align:left;padding:5px 8px;color:#b26a00;">Cliente</th>'
            f'<th style="text-align:left;padding:5px 8px;color:#b26a00;">Gestión</th>'
            f'<th style="text-align:left;padding:5px 8px;color:#b26a00;">Observación</th>'
            f'</tr>{cobranza_rows}</table>'
        )

    # === Proyectos semanales ===
    semanales = [
        (aid, a) for aid, a in week["activities"].items() if a["tipo"] != "diaria"
    ]
    sem_actualizadas, sem_pendientes = [], []
    for aid, a in semanales:
        ult = a.get("ultima_actualizacion") or ""
        if ult.startswith(today_iso):
            sem_actualizadas.append(a)
        elif (a.get("avance") or 0) < 100:
            sem_pendientes.append(a)

    sem_section = ""
    if sem_actualizadas or sem_pendientes:
        rows = ""
        for a in sem_actualizadas:
            prio = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(a.get("priority", "media"), "")
            notas = a.get("notas") or ""
            rows += (
                f'<tr style="background:#e8f5e9;">'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;">{prio} {a["nombre"]}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right;color:#2e7d32;font-weight:600;">{a.get("avance", 0):.0f}%</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555;">{notas or "—"}</td>'
                f'</tr>'
            )
        for a in sem_pendientes:
            prio = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(a.get("priority", "media"), "")
            rows += (
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;color:#777;">{prio} {a["nombre"]}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;text-align:right;color:#777;">{(a.get("avance") or 0):.0f}%</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#999;font-style:italic;">sin avance hoy</td>'
                f'</tr>'
            )
        sem_section = (
            f'<h4 style="color:#0e7c39;margin:14px 0 4px 0;">📌 Proyectos semanales</h4>'
            f'<table style="width:100%;border-collapse:collapse;font-size:13px;'
            f'background:#fff;border:1px solid #d9e0d9;border-radius:4px;">'
            f'<tr style="background:#f4faf6;">'
            f'<th style="text-align:left;padding:5px 8px;color:#0e7c39;">Proyecto</th>'
            f'<th style="text-align:right;padding:5px 8px;color:#0e7c39;">Avance</th>'
            f'<th style="text-align:left;padding:5px 8px;color:#0e7c39;">Notas</th>'
            f'</tr>{rows}</table>'
        )

    # === Cierre de caja COMPLETO con denominaciones (solo para asistentes) ===
    cierre_section = ""
    if es_asistente:
        cierre = activity_state.get_cierre_caja(user_email, today_iso)
        if cierre:
            status = cierre.get("status", "cuadra")
            total = cierre.get("total", 0)
            fondo = cierre.get("fondo_esperado", cierre.get("fondo", 0))
            diferencia = cierre.get("diferencia", 0)
            if status == "cuadra":
                status_color = "#2e7d32"
                status_text = f"✅ Cuadra perfecto · queda en caja ${total:,.2f} (fondo objetivo ${fondo:,.2f})"
            elif status == "sobra":
                status_color = "#ef6c00"
                status_text = (
                    f"⚠️ Sobra ${diferencia:,.2f} · queda en caja "
                    f"${total:,.2f} (fondo objetivo ${fondo:,.2f})"
                )
            else:
                status_color = "#c62828"
                status_text = (
                    f"🔴 Falta ${abs(diferencia):,.2f} · queda en caja "
                    f"${total:,.2f} (fondo objetivo ${fondo:,.2f})"
                )
            # Detalle denominaciones
            detalle_b = cierre.get("detalle_billetes")
            detalle_m = cierre.get("detalle_monedas")
            if not detalle_b or not detalle_m:
                calc = activity_state.calcular_cierre_caja(
                    cierre.get("denoms", {}), sucursal=sucursal
                )
                detalle_b = calc["detalle_billetes"]
                detalle_m = calc["detalle_monedas"]
            denom_rows = ""
            for d in (detalle_b + detalle_m):
                if d.get("cantidad", 0) > 0:
                    denom_rows += (
                        f'<tr>'
                        f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;">'
                        f'{d["label"]} × {d["cantidad"]}</td>'
                        f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;text-align:right;'
                        f'font-family:Consolas,monospace;">${d["subtotal"]:,.2f}</td>'
                        f'</tr>'
                    )
            if not denom_rows:
                denom_rows = '<tr><td colspan="2" style="padding:4px 8px;color:#999;">(sin denominaciones)</td></tr>'

            notas_cierre = cierre.get("notas") or ""
            notas_html = f'<p style="font-size:12px;color:#555;margin:6px 0 0 0;"><b>Notas:</b> {notas_cierre}</p>' if notas_cierre else ""

            cierre_section = (
                f'<h4 style="color:#0e7c39;margin:14px 0 4px 0;">💵 Cierre de caja {sucursal}</h4>'
                f'<div style="padding:8px 12px;background:#fff;border:2px solid {status_color};'
                f'border-radius:6px;margin:4px 0;">'
                f'<div style="color:{status_color};font-weight:700;font-size:14px;">{status_text}</div>'
                f'<table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;">'
                f'{denom_rows}'
                f'<tr><td style="padding:6px 8px;font-weight:700;border-top:2px solid {status_color};">TOTAL CONTADO</td>'
                f'<td style="padding:6px 8px;text-align:right;font-weight:700;font-family:Consolas,monospace;border-top:2px solid {status_color};">${total:,.2f}</td></tr>'
                f'</table>'
                f'{notas_html}'
                f'</div>'
            )
        else:
            cierre_section = (
                f'<h4 style="color:#999;margin:14px 0 4px 0;">💵 Cierre de caja {sucursal}</h4>'
                f'<div style="padding:8px 12px;background:#fff8e1;border-left:4px solid #f57c00;'
                f'border-radius:4px;font-size:13px;color:#666;font-style:italic;">'
                f'No se marcó cierre de caja hoy.'
                f'</div>'
            )

    # === TikTok seguidores (para users con actividad de videos TikTok) ===
    # 2026-06-24: la actividad vigente es "tiktok-videos-diarios" (6/día). Se
    # mantiene compat con la vieja "video-tiktok" (meta 1) por si reaparece.
    tiktok_section = ""
    _tt_act = (
        week.get("activities", {}).get("tiktok-videos-diarios")
        or week.get("activities", {}).get("video-tiktok")
    )
    tiene_tiktok = _tt_act is not None
    if tiene_tiktok:
        tt_actual = activity_state.get_tiktok_seguidores_semana(user_email)
        # tt_anterior = semana pasada para mostrar delta
        from datetime import timedelta as _td
        try:
            wk_str = week.get("wk") or activity_state.week_key()
            year, w = wk_str.split("-W")
            prev_wk = f"{year}-W{int(w) - 1:02d}" if int(w) > 1 else None
        except Exception:
            prev_wk = None
        tt_prev = activity_state.get_tiktok_seguidores_semana(user_email, wk=prev_wk) if prev_wk else None
        if tt_actual:
            actual_n = int(tt_actual.get("seguidores") or 0)
            prev_n = int((tt_prev or {}).get("seguidores") or 0)
            delta = actual_n - prev_n
            if delta > 0:
                delta_html = f' <span style="color:#2e7d32;font-weight:600;">+{delta}</span>'
            elif delta < 0:
                delta_html = f' <span style="color:#c62828;font-weight:600;">{delta}</span>'
            else:
                delta_html = ' <span style="color:#999;">(=)</span>' if prev_wk else ''
            tiktok_section = (
                f'<div style="margin:10px 0;padding:8px 12px;background:#fff;border-left:4px solid #ff0050;'
                f'font-size:13px;">'
                f'📱 <b>TikTok seguidores:</b> '
                f'<span style="font-weight:700;">{actual_n:,}</span>{delta_html}'
                f'</div>'
            )

    # === Videos TikTok de la semana (meta 5/semana, users con video-tiktok) ===
    # Métrica de seguimiento pedida por gerencia (2026-06-19): muestra cuántos
    # videos lleva subidos en la semana contra la meta semanal. El conteo sale
    # de sumar las marcas diarias de la activity "video-tiktok" (cada video = 1).
    tiktok_videos_section = ""
    tt_video_act = _tt_act
    if tt_video_act:
        # Meta semanal: la del entry si está; si no, la diaria × 5 días
        # laborales (ej. tiktok-videos-diarios = 6/día → 30/semana). Fallback 5.
        _meta_sem = tt_video_act.get("meta_semanal")
        if not _meta_sem:
            _meta_diaria = int(tt_video_act.get("meta") or 0)
            _meta_sem = _meta_diaria * 5 if _meta_diaria else 5
        meta_videos = int(_meta_sem)
        videos_hechos = 0
        for log_entry in (tt_video_act.get("log") or {}).values():
            videos_hechos += int(float(log_entry.get("valor") or 0))
        completo = videos_hechos >= meta_videos
        vid_color = "#2e7d32" if completo else "#ef6c00"
        check = " ✅" if completo else ""
        tiktok_videos_section = (
            f'<div style="margin:10px 0;padding:8px 12px;background:#fff;border-left:4px solid #ff0050;'
            f'font-size:13px;">'
            f'🎬 <b>Meta TikTok:</b> {meta_videos} videos · '
            f'<span style="color:{vid_color};font-weight:700;">'
            f'TikTok: {videos_hechos}/{meta_videos} completados{check}</span>'
            f'</div>'
        )

    # === Chocolates (solo asistentes) ===
    choco_section = ""
    if es_asistente:
        choco = activity_state.get_chocolates_semana(user_email)
        if choco and choco.get("stock_inicial"):
            stock_actual = choco.get("stock_actual", 0)
            color_choco = "#c62828" if stock_actual <= 5 else "#2e7d32"
            warn = " ⚠️" if stock_actual <= 5 else ""
            choco_section = (
                f'<div style="margin:10px 0;padding:8px 12px;background:#fff;border-left:4px solid {color_choco};'
                f'font-size:13px;">'
                f'🍫 <b>Stock de chocolates:</b> '
                f'<span style="color:{color_choco};font-weight:700;">{stock_actual}{warn}</span>'
                f' <span style="color:#888;font-size:12px;">(inicial {choco.get("stock_inicial", 0)} '
                f'+ recargas {choco.get("total_recargado", 0)} − entregas {choco.get("total_entregado", 0)})</span>'
                f'</div>'
            )

    # === Bloque completo ===
    if not daily_section and not sem_section and not cierre_section and not choco_section and not tiktok_section and not tiktok_videos_section:
        body_block = (
            '<p style="color:#999;font-style:italic;font-size:13px;">'
            '(Sin actividad registrada hoy)</p>'
        )
    else:
        body_block = horario_html + daily_section + sem_section + cierre_section + tiktok_videos_section + tiktok_section + choco_section

    # Header color por rol
    if es_asistente:
        header_color = "#0e7c39"  # verde corporativo
        header_bg = "#f4faf6"
    else:
        header_color = "#1565c0"  # azul para internos
        header_bg = "#e3f2fd"

    return (
        f'<div style="margin:28px 0;border:2px solid {header_color};border-radius:8px;overflow:hidden;'
        f'background:#fff;">'
        f'<div style="background:{header_color};color:#fff;padding:10px 16px;font-weight:700;font-size:16px;">'
        f'{titulo_bloque}'
        f'</div>'
        f'<div style="padding:14px 18px;background:{header_bg};">'
        f'<div style="margin-bottom:8px;font-size:13px;">{horario_html}</div>'
        f'{daily_section}{cobranza_section}{sem_section}{cierre_section}{tiktok_videos_section}{tiktok_section}{choco_section}'
        f'</div>'
        f'</div>'
    )


# Fase 5: _collaborator_block_html (v1) ELIMINADA — dead code (~189 líneas),
# solo se usa _collaborator_block_html_v2 (auditoría C8).

# ============ Consolidated daily summary (Phase O, 2026-06-02) ============
CONSOLIDATED_DAILY_TO_DEFAULT = ",".join(core_config.JEFE)
CONSOLIDATED_DAILY_CC_DEFAULT = core_config.MIO
# Identidad/roles ahora salen de core_config (single source, tenant-overridable).
# Antes estaban hardcodeados aquí; los valores legacy de Biodegradables son
# idénticos (lo fija test_tenant_config_biodegradables).
SUPERVISORS_ONLY_EMAILS = core_config.SUPERVISORS_ONLY_EMAILS  # = teams_bot.SUPERVISORS_ONLY
ASISTENTE_EMAILS = core_config.ASISTENTE_EMAILS

# Chofer/repartidor (José en Biodegradables): bloque dedicado en el consolidado
# con su data de logística (entregas, salidas, caja chica). NO está en
# ASISTENTE_EMAILS porque su state es diferente (sin cierre de caja con
# denominaciones, etc.).
JOSE_EMAIL_CONS = next(iter(core_config.CHOFER_EMAILS), "")
CAJA_CHICA_ALERTA_JOSE = 30.0

# ===== Rotación de asistentes los sábados (2026-06-15) =====
# En la sucursal del chofer hay 2 asistentes que se turnan los sábados
# (Asistente 1 = caja/sucursal, Asistente 2 = chofer/logística). Si el rotativo
# no llena el reporte del sábado, se asume AUSENCIA ESPERADA por turno — no es
# error ni pendiente. Solo aplica al recap del sábado (lunes 8 AM). Derivado de
# core_config: asistente 1 de la sucursal del chofer + el set de rotativos.
_CHOFER_SUCURSAL = core_config.sucursal_for(JOSE_EMAIL_CONS)
GYE_ASISTENTE1_EMAIL = next(
    (e for e, p in core_config.PEOPLE.items()
     if p.get("role") == "asistente"
     and p.get("sucursal") == _CHOFER_SUCURSAL
     and p.get("asistente_num") == 1),
    "",
)
GYE_ROTATIVOS_SABADO = set(core_config.ROTATIVOS_SABADO_EMAILS)


def _gye_sin_reporte_dia(email: str, fecha_iso: str) -> bool:
    """True si el asistente GYE no registró NADA ese día (horario, cierre ni
    marca diaria). Se usa para interpretar el sábado como ausencia esperada
    por rotación, en vez de mostrarlo como pendiente/error."""
    if activity_state.get_day_schedule(email, fecha_iso):
        return False
    if activity_state.get_cierre_caja(email, fecha_iso):
        return False
    try:
        wk = activity_state.week_key(date.fromisoformat(fecha_iso))
        week = activity_state.get_week(email, wk=wk)
    except Exception:
        return True
    for a in (week.get("activities") or {}).values():
        if a.get("tipo") == "diaria" and (a.get("log") or {}).get(fecha_iso) is not None:
            return False
    return True


def _ausencia_rotativa_block_html(titulo_bloque: str, fecha_fmt: str) -> str:
    """Bloque neutro (no rojo) para un asistente GYE ausente por turno rotativo
    el sábado. Mismo marco verde que los demás asistentes, sin alarma."""
    header_color = "#0e7c39"
    return (
        f'<div style="margin:28px 0;border:2px solid {header_color};border-radius:8px;'
        f'overflow:hidden;background:#fff;">'
        f'<div style="background:{header_color};color:#fff;padding:10px 16px;'
        f'font-weight:700;font-size:16px;">{titulo_bloque}</div>'
        f'<div style="padding:14px 18px;background:#f4faf6;">'
        f'<p style="margin:0;font-size:13px;color:#555;">{fecha_fmt}</p>'
        f'<p style="margin:8px 0 0;font-size:13px;color:#666;">'
        f'🔁 <b>Ausencia esperada</b> — turno rotativo de sábado. '
        f'Este asistente no estaba programado para asistir; no es un reporte '
        f'pendiente.</p>'
        f'</div></div>'
    )


def _jose_consolidated_block_html(today_iso: str | None = None) -> str:
    """Phase V (2026-06-10): bloque de José para el consolidado diario.

    Render compacto con:
      - Header verde "📦 ASISTENTE 2 GYE — José Solórzano"
      - Salidas del día (inicio, fin, duración, entregas por salida)
      - Tabla de entregas (cliente, dirección final, monto, estado, pago, obs)
      - Caja chica: inicial, gastos del día, reposiciones, saldo (rojo ≤ $30)
    """
    from html import escape
    today_iso = today_iso or _hoy_ec().isoformat()
    fecha_d = date.fromisoformat(today_iso)
    fecha_fmt = fecha_d.strftime("%d/%m/%Y")
    # Header del chofer desde core_config (no hardcode de nombre real).
    _ch = core_config.PEOPLE.get(JOSE_EMAIL_CONS, {})
    _chofer_label = (
        f"📦 ASISTENTE {_ch.get('asistente_num') or 2} "
        f"{_ch.get('sucursal') or 'GYE'} — {_ch.get('name') or 'Chofer'}"
    )

    ruta = activity_state.get_ruta_dia(JOSE_EMAIL_CONS, today_iso)
    salidas = ruta.get("salidas", []) or []
    entregas = activity_state.get_entregas_consolidadas_dia(JOSE_EMAIL_CONS, today_iso) or {}
    cc = activity_state.get_caja_chica(JOSE_EMAIL_CONS) or {"inicial": None, "saldo": 0.0, "movimientos": []}
    movs_hoy = activity_state.caja_chica_movimientos_dia(JOSE_EMAIL_CONS, today_iso) or []
    horario = activity_state.get_day_schedule(JOSE_EMAIL_CONS, today_iso)

    # Rotación GYE de sábados: si José (Asistente 2 GYE) no registró ruta,
    # envíos ni movimientos un sábado, es ausencia esperada por el turno
    # rotativo — no un reporte pendiente.
    if fecha_d.weekday() == 5 and not salidas and not entregas and not movs_hoy and not horario:
        return _ausencia_rotativa_block_html(_chofer_label, fecha_fmt)

    # Resumen de entregas
    n_entregadas = sum(1 for e in entregas.values() if e.get("status") == "entregado")
    n_no_entregadas = sum(1 for e in entregas.values() if e.get("status") == "no_entregado")
    n_pendientes = sum(1 for e in entregas.values() if e.get("status") == "pendiente")
    summary_chips = (
        f'<span style="color:#0d8a3f;font-weight:600">✅ {n_entregadas}</span>  ·  '
        f'<span style="color:#c53030;font-weight:600">❌ {n_no_entregadas}</span>  ·  '
        f'<span style="color:#999">⏳ {n_pendientes}</span>'
    )

    # Envíos AGRUPADOS POR SALIDA (2026-06-23): el usuario quiere ver qué
    # entregó José en CADA salida que hizo, no una lista plana. Cada salida
    # lista sus propias entregas (lo que José marcó en esa salida); al final,
    # los que nunca salieron quedan en "Pendientes". El detalle del cliente sale
    # del snapshot; el estado/obs/pago de cada entrega, de la salida.
    snapshot = ruta.get("envios_snapshot", {}) or {}

    def _hora_corta(iso: str) -> str:
        return iso[:16].replace("T", " ")[-5:] if iso else "?"

    def _envio_row(fid: str, entr: dict) -> str:
        snap = snapshot.get(fid, {}) or {}
        cliente = escape(snap.get("cliente") or entr.get("cliente") or "?")
        doc = escape(snap.get("documento", "?"))
        total = snap.get("total", 0) or 0
        status = entr.get("status", "pendiente")
        dir_real = entr.get("direccion_real") or snap.get("direccion_factura") or ""
        obs = entr.get("observacion") or ""
        razon = entr.get("razon_no_entrega") or ""
        pago = entr.get("pago_envio") or 0
        if status == "entregado":
            badge = '<span style="color:#0d8a3f;font-weight:600">✅ Entregado</span>'
        elif status == "no_entregado":
            badge = (
                '<span style="color:#c53030;font-weight:600">❌ No entregado</span>'
                + (f'<br><small style="color:#777">{escape(razon)}</small>' if razon else "")
            )
        else:
            badge = '<span style="color:#999">⏳ Pendiente</span>'
        pago_html = f"${pago:,.2f}" if pago else "—"
        obs_html = f'<br><small style="color:#777">{escape(obs)}</small>' if obs else ""
        if snap.get("adhoc"):
            tipo_a = (snap.get("tipo_adhoc") or "entrega").upper()
            doc_extra = f'<br><small style="color:#e67e22;font-weight:600">➕ AD-HOC ({tipo_a})</small>'
        else:
            doc_extra = ""
        total_html = f"${total:,.2f}" if total > 0 else "—"
        return (
            "<tr>"
            f"<td>{cliente}<br><small style='color:#777'>{doc}</small>{doc_extra}</td>"
            f"<td style='text-align:right'>{total_html}</td>"
            f"<td>{escape(dir_real)}</td>"
            f"<td style='text-align:right'>{pago_html}</td>"
            f"<td>{badge}{obs_html}</td>"
            "</tr>"
        )

    _TH = (
        '<tr style="background:#f0f0f0">'
        '<th style="padding:6px 8px;text-align:left">Cliente</th>'
        '<th style="padding:6px 8px;text-align:right">Monto</th>'
        '<th style="padding:6px 8px;text-align:left">Dirección final</th>'
        '<th style="padding:6px 8px;text-align:right">Pago terminal</th>'
        '<th style="padding:6px 8px;text-align:left">Estado</th>'
        '</tr>'
    )

    def _tabla(rows: str) -> str:
        return (
            '<table style="width:100%;border-collapse:collapse;font-size:13px;'
            'margin:4px 0 12px">' + _TH + rows + '</table>'
        )

    def _dur_min(ini_iso: str, fin_iso: str) -> str:
        try:
            d1 = datetime.fromisoformat((ini_iso or "").replace("Z", "+00:00"))
            d2 = datetime.fromisoformat((fin_iso or "").replace("Z", "+00:00"))
            return f"{int((d2 - d1).total_seconds() / 60)} min"
        except Exception:
            return ""

    secciones = ""
    fids_vistos: set[str] = set()
    real_idx = 0
    for s in salidas:
        entr_dict = s.get("entregas", {}) or {}
        en_oficina = s.get("marcado_en_oficina")
        if en_oficina and not entr_dict:
            continue
        n_ok = sum(1 for e in entr_dict.values() if e.get("status") == "entregado")
        n_no = sum(1 for e in entr_dict.values() if e.get("status") == "no_entregado")
        if en_oficina:
            cabecera = f"🏢 Entregado en oficina (sin salir a ruta) · ✅ {n_ok}"
            if n_no:
                cabecera += f" · ❌ {n_no}"
        else:
            real_idx += 1
            ini = _hora_corta(s.get("inicio_ts", ""))
            if s.get("fin_ts"):
                dur = _dur_min(s.get("inicio_ts", ""), s.get("fin_ts"))
                tramo = f"{ini} → {_hora_corta(s.get('fin_ts'))}" + (f" ({dur})" if dur else "")
            else:
                tramo = f"{ini} → <span style='color:#c53030'>sin cerrar</span>"
            cabecera = f"🚗 Salida #{real_idx} · {tramo} · ✅ {n_ok} entregadas"
            if n_no:
                cabecera += f" · ❌ {n_no} no entregadas"
            if not entr_dict:
                cabecera += " · <span style='color:#999'>sin entregas marcadas</span>"
        rows = "".join(_envio_row(fid, entr) for fid, entr in entr_dict.items())
        fids_vistos.update(entr_dict.keys())
        secciones += f'<p style="margin:12px 0 2px;font-weight:600;color:#0e7c39">{cabecera}</p>'
        if rows:
            secciones += _tabla(rows)

    # Aún sin entregar: en el snapshot pero nunca marcados en ninguna salida
    pend_fids = [fid for fid in snapshot if fid not in fids_vistos]
    if pend_fids:
        rows = "".join(_envio_row(fid, {"status": "pendiente"}) for fid in pend_fids)
        secciones += (
            '<p style="margin:12px 0 2px;font-weight:600;color:#c53030">'
            f'⏳ Aún sin entregar — quedaron pendientes ({len(pend_fids)})</p>'
            + _tabla(rows)
        )

    if secciones:
        envios_html = (
            '<p style="margin:8px 0 4px;font-weight:700;color:#444">'
            '📦 Envíos por salida</p>' + secciones
        )
    else:
        envios_html = (
            '<p style="color:#999;font-size:12px;margin:8px 0;">'
            'José no salió a ruta ni cargó envíos hoy.</p>'
        )

    # NOTA (2026-06-19): se eliminó la sección dedicada "📝 Observaciones de
    # José" que se renderizaba aparte debajo de la tabla. Las observaciones (y
    # las razones de no entrega) ya se muestran inline en la columna "Estado"
    # de la tabla de envíos — esa es la ÚNICA fuente. No reintroducir una
    # sección de observaciones separada: duplicaba la misma información.

    # Caja chica
    saldo = float(cc.get("saldo") or 0)
    gastos_dia = sum(float(m.get("monto") or 0) for m in movs_hoy if m.get("tipo") == "gasto")
    repos_dia = sum(float(m.get("monto") or 0) for m in movs_hoy if m.get("tipo") == "reposicion")
    # Saldo con el que José ARRANCÓ hoy = saldo actual revirtiendo los
    # movimientos de hoy (= cierre del día anterior). Fix 2026-06-25: antes se
    # mostraba el `inicial` fijo (siempre $69.53), no el saldo real de arranque.
    saldo_inicio_dia = saldo - repos_dia + gastos_dia
    saldo_color = "#c53030" if saldo <= CAJA_CHICA_ALERTA_JOSE else (
        "#e67e22" if saldo <= CAJA_CHICA_ALERTA_JOSE * 2 else "#0d8a3f"
    )
    alerta_extra = (
        ' <span style="background:#ffe5e5;color:#c53030;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">⚠️ BAJO — pedir reposición</span>'
        if saldo <= CAJA_CHICA_ALERTA_JOSE else ""
    )
    caja_html = (
        f'<p style="margin:8px 0 4px;font-weight:600;color:#444">💵 Caja chica</p>'
        f'<p style="margin:4px 0;font-size:14px">'
        f'Saldo inicial del día <span style="color:#888;font-size:11px">(cierre de ayer)</span>: '
        f'<b>${saldo_inicio_dia:,.2f}</b>  ·  '
        f'Gastos hoy: <b style="color:#c53030">-${gastos_dia:,.2f}</b>  ·  '
        f'Reposiciones hoy: <b style="color:#0d8a3f">+${repos_dia:,.2f}</b><br>'
        f'<span style="font-size:16px;font-weight:700;color:{saldo_color}">'
        f'Saldo actual: ${saldo:,.2f}</span>{alerta_extra}</p>'
    )

    # Asistencia / horario (2026-06-23): José la marca con "💾 Guardar
    # asistencia" en su card de ruta (intent jose_asistencia → set_day_schedule).
    # Antes no se mostraba en el consolidado; ahora aparece como los demás.
    if horario is None:
        asistencia_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">⏰ Asistencia</p>'
            '<p style="margin:4px 0;font-size:13px;color:#999;font-style:italic">'
            'Sin reportar hoy.</p>'
        )
    elif horario.get("estandar"):
        asistencia_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">⏰ Asistencia</p>'
            '<p style="margin:4px 0;font-size:14px;color:#2e7d32;font-weight:600">'
            f'{activity_state.horario_estandar_label(today_iso)} (estándar) ✅</p>'
        )
    else:
        desde = horario.get("desde") or "?"
        hasta = horario.get("hasta") or "?"
        razon = horario.get("razon") or "sin razón especificada"
        asistencia_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">⏰ Asistencia</p>'
            '<p style="margin:4px 0;font-size:14px;color:#ef6c00;font-weight:600">'
            f'{desde} – {hasta} (no estándar)</p>'
            f'<p style="margin:2px 0;font-size:12px;color:#666">Razón: {escape(razon)}</p>'
        )

    # Actividades delegadas por gerencia (2026-06-25): que Daniel vea en el 6:30
    # lo que José marcó de las tareas asignadas. Diarias (no cobranza) +
    # semanales no finalizadas. Vacío si no tiene ninguna.
    try:
        _acts_jose = activity_state.get_week(JOSE_EMAIL_CONS).get("activities", {})
    except Exception:
        _acts_jose = {}
    _diar = [(aid, a) for aid, a in _acts_jose.items()
             if a.get("tipo") == "diaria" and not aid.startswith("cobranza-")]
    _sem = [(aid, a) for aid, a in _acts_jose.items()
            if a.get("tipo") != "diaria"
            and activity_state.task_effective_status(a) != "finalizada"]
    actividades_html = ""
    if _diar or _sem:
        _filas = ""
        for aid, a in _diar:
            rec = (a.get("log") or {}).get(today_iso)
            meta = a.get("meta")
            if rec is None:
                ic, est, col, det = "⏳", "Sin marcar", "#999", "—"
            else:
                val = rec.get("valor", 0) or 0
                nota = (rec.get("notas") or "").strip()
                if val == 0:
                    ic, est, col = "❌", "No hecha", "#c62828"
                elif meta and val < meta:
                    ic, est, col = "⚠️", "Parcial", "#ef6c00"
                else:
                    ic, est, col = "✅", "Hecha", "#2e7d32"
                det = (f"{val:g}" + (f"/{meta}" if meta else "")) if val else ""
                if nota:
                    det += (" · " if det else "") + f'<i>"{escape(nota)}"</i>'
                det = det or "—"
            _filas += (
                f'<tr><td style="padding:4px 6px">{escape(a.get("nombre", aid))}</td>'
                f'<td style="padding:4px 6px;color:{col};font-weight:600;white-space:nowrap">{ic} {est}</td>'
                f'<td style="padding:4px 6px;font-size:12px;color:#555">{det}</td></tr>'
            )
        for aid, a in _sem:
            av = a.get("avance") or 0
            col = "#2e7d32" if av >= 100 else "#ef6c00"
            _filas += (
                f'<tr><td style="padding:4px 6px">{escape(a.get("nombre", aid))}</td>'
                f'<td style="padding:4px 6px;color:{col};font-weight:600;white-space:nowrap">📊 {av:.0f}%</td>'
                f'<td style="padding:4px 6px;font-size:12px;color:#555">proyecto</td></tr>'
            )
        actividades_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">📋 Actividades asignadas</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:10px">'
            '<tr style="background:#f0f0f0">'
            '<th style="padding:4px 6px;text-align:left">Actividad</th>'
            '<th style="padding:4px 6px;text-align:left">Estado</th>'
            '<th style="padding:4px 6px;text-align:left">Detalle</th></tr>'
            + _filas + '</table>'
        )

    # Phase V (2026-06-11): mismo estilo que _collaborator_block_html_v2
    # (header verde con bg sólido, body con bg suave, border 2px verde).
    header_color = "#0e7c39"  # mismo verde corporativo que los Asistentes 1
    header_bg = "#f4faf6"
    return (
        f'<div style="margin:28px 0;border:2px solid {header_color};'
        f'border-radius:8px;overflow:hidden;background:#fff;">'
        f'<div style="background:{header_color};color:#fff;padding:10px 16px;'
        f'font-weight:700;font-size:16px;">'
        f'{_chofer_label}'
        f'</div>'
        f'<div style="padding:14px 18px;background:{header_bg};">'
        f'<p style="margin:0 0 10px 0;font-size:13px;color:#555;">'
        f'{fecha_fmt}  ·  {summary_chips}</p>'
        f'{asistencia_html}'
        f'{actividades_html}'
        f'{envios_html}'
        f'{caja_html}'
        f'</div>'
        f'</div>'
    )


def _classify_dailies(user_email: str, today_iso: str, wk: str | None = None) -> dict:
    """Cuenta hechas/parciales/no_hechas/sin_marcar de un user para hoy.

    Retorna también listas con las items problemáticas (no hechas + parciales)
    con sus razones, para mostrar en sección destacada.

    `wk` (semana ISO) permite leer una semana distinta a la actual — necesario
    para el recap del sábado que corre el lunes (el sábado cae en la semana
    ISO anterior). Si es None, usa la semana actual (comportamiento histórico).
    """
    week = activity_state.get_week(user_email, wk=wk)
    diarias = [
        (aid, a) for aid, a in week["activities"].items() if a["tipo"] == "diaria"
    ]
    counts = {"hechas": 0, "parciales": 0, "no_hechas": 0, "sin_marcar": 0}
    problematicas = []  # cada elem: dict con nombre, estado, razon, priority

    # Dedup cobranzas por cliente (2026-06-25): los aids `cobranza-<cliente>-<fecha>`
    # acumulaban el mismo cliente → se contaba y listaba repetido en
    # "Lo que requiere seguimiento". Colapsamos a UNA entrada por cliente con su
    # estado agregado de hoy (contactado si alguna marca tiene valor > 0).
    cob_por_cliente: dict = {}
    no_cobranzas = []
    for aid, a in diarias:
        if not aid.startswith("cobranza-"):
            no_cobranzas.append((aid, a))
            continue
        nombre_full = a.get("nombre", aid)
        _cli = nombre_full.replace("📞 Cobranza:", "").strip()
        cli_key = _cli.split(" — ")[0].strip() if " — " in _cli else _cli
        rec = (a.get("log") or {}).get(today_iso)
        cur = cob_por_cliente.setdefault(cli_key, {
            "nombre": nombre_full, "priority": a.get("priority", "media"),
            "valor": None, "razon": "", "marcada": False,
        })
        if rec is not None:
            cur["marcada"] = True
            v = rec.get("valor", 0) or 0
            if cur["valor"] is None or v > (cur["valor"] or 0):
                cur["valor"] = v
            rz = (rec.get("notas") or "").strip()
            if rz and not cur["razon"]:
                cur["razon"] = rz

    for info in cob_por_cliente.values():
        nombre = info["nombre"]
        priority = info["priority"]
        if not info["marcada"]:
            counts["sin_marcar"] += 1
            problematicas.append({
                "nombre": nombre, "estado": "sin_marcar",
                "razon": "", "priority": priority,
            })
        elif (info["valor"] or 0) == 0:
            counts["no_hechas"] += 1
            problematicas.append({
                "nombre": nombre, "estado": "no_hecha",
                "razon": info["razon"], "priority": priority,
            })
        else:
            counts["hechas"] += 1  # cobranza meta=1, valor>0 → contactada

    for aid, a in no_cobranzas:
        rec = (a.get("log") or {}).get(today_iso)
        meta = a.get("meta")
        priority = a.get("priority", "media")
        nombre = a.get("nombre", aid)
        if rec is None:
            counts["sin_marcar"] += 1
            problematicas.append({
                "nombre": nombre, "estado": "sin_marcar",
                "razon": "", "priority": priority,
            })
            continue
        valor = rec.get("valor", 0) or 0
        razon = (rec.get("notas") or "").strip()
        if valor == 0:
            counts["no_hechas"] += 1
            problematicas.append({
                "nombre": nombre, "estado": "no_hecha",
                "razon": razon, "priority": priority,
            })
        elif meta and valor < meta:
            counts["parciales"] += 1
            problematicas.append({
                "nombre": nombre, "estado": "parcial",
                "razon": razon, "priority": priority,
                "valor": valor, "meta": meta,
            })
        else:
            counts["hechas"] += 1

    # Semanales sin avance hoy (solo para colaboradores no-asistente)
    semanales_sin_avance = []
    for aid, a in week["activities"].items():
        if a.get("tipo") == "diaria":
            continue
        ult = a.get("ultima_actualizacion") or ""
        if not ult.startswith(today_iso) and (a.get("avance") or 0) < 100:
            semanales_sin_avance.append({
                "nombre": a.get("nombre", aid),
                "avance": a.get("avance", 0),
                "priority": a.get("priority", "media"),
            })
    return {
        "counts": counts,
        "problematicas": problematicas,
        "semanales_sin_avance": semanales_sin_avance,
    }


def _executive_summary_table(collaborator_data: list[dict]) -> str:
    """Tabla compacta arriba: 1 fila por colaborador con su scorecard del día."""
    rows = ""
    for d in collaborator_data:
        alias = d["alias"]
        counts = d["counts"]
        hora = d["hora"]
        cierre_html = d.get("cierre_html", "—")
        choco_html = d.get("choco_html", "—")
        # Total problemáticas para destacar en rojo si hay
        total_prob = counts["parciales"] + counts["no_hechas"] + counts["sin_marcar"]
        row_color = "#fff3e0" if total_prob > 0 else "#ffffff"
        rows += (
            f'<tr style="background:{row_color};">'
            f'<td style="padding:6px 8px;font-weight:600;">{alias}</td>'
            f'<td style="padding:6px 8px;font-size:12px;color:#666;">{hora}</td>'
            f'<td style="padding:6px 8px;text-align:center;color:#2e7d32;font-weight:600;">{counts["hechas"]}</td>'
            f'<td style="padding:6px 8px;text-align:center;color:#ef6c00;font-weight:600;">{counts["parciales"]}</td>'
            f'<td style="padding:6px 8px;text-align:center;color:#c62828;font-weight:600;">{counts["no_hechas"]}</td>'
            f'<td style="padding:6px 8px;text-align:center;color:#999;">{counts["sin_marcar"]}</td>'
            f'<td style="padding:6px 8px;text-align:right;font-family:Consolas,monospace;font-size:12px;">{cierre_html}</td>'
            f'</tr>'
        )
    return (
        '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-top:6px;">'
        '<thead><tr style="background:#0e7c39;color:white;">'
        '<th style="text-align:left;padding:6px 8px;">Colaborador</th>'
        '<th style="text-align:left;padding:6px 8px;">Horario</th>'
        '<th style="text-align:center;padding:6px 8px;">✅</th>'
        '<th style="text-align:center;padding:6px 8px;">⚠️</th>'
        '<th style="text-align:center;padding:6px 8px;">❌</th>'
        '<th style="text-align:center;padding:6px 8px;">⏳</th>'
        '<th style="text-align:right;padding:6px 8px;">Cierre caja</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _sin_marcar_section(collaborator_data: list[dict]) -> str:
    """Phase V (2026-06-11): alerta ROJA arriba del consolidado cuando algún
    colaborador NO llenó NADA del bot hoy.

    Criterio: hora="sin reportar" Y todas las counts en 0 excepto "sin_marcar".
    """
    sin_llenar: list[str] = []
    for d in collaborator_data:
        counts = d.get("counts") or {}
        hora_html = d.get("hora", "")
        hechas = counts.get("hechas", 0)
        parciales = counts.get("parciales", 0)
        no_hechas = counts.get("no_hechas", 0)
        sin_marcar = counts.get("sin_marcar", 0)
        total_activities = hechas + parciales + no_hechas + sin_marcar
        # No tiene horario reportado Y nada marcado entre hechas/parciales/no_hechas
        sin_horario = "sin reportar" in hora_html
        nada_marcado = (hechas + parciales + no_hechas) == 0
        # Solo lo flageamos si tiene actividades pendientes (sin_marcar > 0).
        # Si no tiene ninguna, no es "no llenó", es "no tiene nada".
        if sin_horario and nada_marcado and sin_marcar > 0:
            sin_llenar.append(d["alias"])

    if not sin_llenar:
        return ""

    nombres = ", ".join(f"<b>{n}</b>" for n in sin_llenar)
    return (
        '<div style="background:#ffe5e5;border:2px solid #c53030;'
        'border-radius:8px;padding:14px 18px;margin:0 0 18px;">'
        '<h3 style="color:#c53030;margin:0 0 8px;font-size:16px;">'
        '🚨 NO LLENARON EL BOT HOY'
        '</h3>'
        f'<p style="margin:0;font-size:14px;color:#7a1a1a;">'
        f'Estos colaboradores no marcaron actividades, horario ni cierre hoy: '
        f'{nombres}.<br>'
        f'<small style="color:#9a4040;">'
        f'Si tenían pendientes, no quedan registradas. Llamada de atención.'
        f'</small></p></div>'
    )


def _problemas_section(collaborator_data: list[dict]) -> str:
    """Sección destacada de actividades problemáticas (no hechas + parciales).

    Esto es lo accionable que Daniel/Gabriela quieren ver al primer vistazo.
    """
    rows = ""
    for d in collaborator_data:
        alias = d["alias"]
        for prob in d.get("problematicas", []):
            estado = prob["estado"]
            if estado == "sin_marcar":
                # No mostrar las sin marcar en esta sección (ya están en tabla resumen)
                continue
            estado_icon = {"no_hecha": "❌", "parcial": "⚠️"}.get(estado, "")
            estado_label = {"no_hecha": "No hecha", "parcial": "Parcial"}.get(estado, "")
            estado_color = {"no_hecha": "#c62828", "parcial": "#ef6c00"}.get(estado, "#666")
            priority = prob.get("priority", "media")
            prio_badge = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(priority, "")
            razon = prob.get("razon") or '<span style="color:#bbb;">sin razón especificada</span>'
            if estado == "parcial" and "valor" in prob:
                razon = f"{prob['valor']:g}/{prob['meta']} — {razon}"
            rows += (
                f'<tr>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-weight:600;color:#555;">{alias}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;">{prio_badge} {prob["nombre"]}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;color:{estado_color};font-weight:600;white-space:nowrap;">{estado_icon} {estado_label}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;color:#555;font-style:italic;">{razon}</td>'
                f'</tr>'
            )
    if not rows:
        return ""
    return (
        '<h3 style="color:#c62828;margin-top:24px;margin-bottom:6px;">'
        '🚨 Lo que requiere seguimiento (no hechas y parciales)</h3>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#fff3e0;color:#c62828;">'
        '<th style="text-align:left;padding:5px 8px;">Colaborador</th>'
        '<th style="text-align:left;padding:5px 8px;">Actividad</th>'
        '<th style="text-align:left;padding:5px 8px;">Estado</th>'
        '<th style="text-align:left;padding:5px 8px;">Razón / detalle</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _asistente_column_html(user_email: str, today_iso: str) -> str:
    """Bloque para una sucursal (info@ o quito@) — cobranzas + cierre + chocolates."""
    week = activity_state.get_week(user_email)
    sucursal = core_config.sucursal_name_for(user_email) or "Quito"
    icon = "📍"

    # Cobranzas: ids que empiezan con "cobranza-". DEDUP por cliente (2026-06-25):
    # auto_assign crea un aid `cobranza-<cliente>-<fecha>` por día, así que el
    # MISMO cliente se acumulaba varias veces en la semana y aparecía repetido en
    # el reporte. Acá colapsamos a UNA entrada por cliente, con el estado y la
    # observación de HOY (contactada si alguna marca de hoy tiene valor > 0).
    por_cliente: dict = {}
    for aid, a in week["activities"].items():
        if not aid.startswith("cobranza-"):
            continue
        nombre = a.get("nombre", "")
        cliente = nombre.replace("📞 Cobranza:", "").strip()
        cliente_corto = cliente.split(" — ")[0].strip() if " — " in cliente else cliente
        info = por_cliente.setdefault(cliente_corto, {"contactada": False, "razon": ""})
        rec = (a.get("log") or {}).get(today_iso)
        if rec is not None:
            valor = rec.get("valor", 0) or 0
            razon = (rec.get("notas") or "").strip()
            if valor > 0:
                info["contactada"] = True
            if razon and not info["razon"]:
                info["razon"] = razon

    cobranzas_contactadas: list[str] = []
    cobranzas_pendientes: list[str] = []
    for cliente_corto, info in por_cliente.items():
        razon = info["razon"]
        if info["contactada"]:
            line = f"<li>{cliente_corto}"
            if razon:
                line += f' <span style="color:#666;font-style:italic;font-size:11px;">"{razon}"</span>'
            line += "</li>"
            cobranzas_contactadas.append(line)
        else:
            line = f"<li>{cliente_corto}"
            if razon:
                line += f' <span style="color:#999;font-style:italic;font-size:11px;">({razon})</span>'
            line += "</li>"
            cobranzas_pendientes.append(line)

    cobranzas_html = ""
    if cobranzas_contactadas or cobranzas_pendientes:
        cobranzas_html += (
            '<div style="margin-top:8px;">'
            '<b style="color:#0e7c39;">📞 Cobranzas</b><br>'
        )
        cobranzas_html += (
            f'<span style="font-size:12px;color:#2e7d32;">✅ Contactadas: {len(cobranzas_contactadas)}</span><br>'
        )
        if cobranzas_contactadas:
            cobranzas_html += (
                f'<ul style="margin:2px 0 6px 18px;padding:0;font-size:12px;color:#444;">'
                f'{"".join(cobranzas_contactadas)}</ul>'
            )
        cobranzas_html += (
            f'<span style="font-size:12px;color:#c62828;">❌ Pendientes: {len(cobranzas_pendientes)}</span>'
        )
        if cobranzas_pendientes:
            cobranzas_html += (
                f'<ul style="margin:2px 0 6px 18px;padding:0;font-size:12px;color:#666;">'
                f'{"".join(cobranzas_pendientes)}</ul>'
            )
        cobranzas_html += "</div>"

    # Cierre de caja
    cierre_html = ""
    cierre = activity_state.get_cierre_caja(user_email, today_iso)
    if cierre:
        cierre_html = (
            f'<div style="margin-top:10px;padding:6px 10px;background:#f4faf6;'
            f'border-left:3px solid #0e7c39;font-size:12px;">'
            f'<b style="color:#0e7c39;">💵 Cierre caja:</b> '
            f'Total ${cierre["total"]:,.2f} → Entregado <b>${cierre["entregado"]:,.2f}</b>'
            f' <span style="color:#888;">(ver correo aparte)</span></div>'
        )
    else:
        cierre_html = (
            '<div style="margin-top:10px;font-size:12px;color:#999;font-style:italic;">'
            '💵 Cierre de caja: no marcado hoy</div>'
        )

    # Chocolates
    choco_html = ""
    choco = activity_state.get_chocolates_semana(user_email)
    if choco and choco.get("stock_inicial"):
        stock_actual = choco.get("stock_actual", 0)
        color = "#c62828" if stock_actual <= 5 else "#0e7c39"
        warn = " ⚠️" if stock_actual <= 5 else ""
        choco_html = (
            f'<div style="margin-top:6px;font-size:12px;color:{color};">'
            f'🍫 Stock chocolates: <b>{stock_actual}</b>{warn}</div>'
        )

    alias = user_email.split("@")[0]
    return (
        f'<div style="background:#fff;border:1px solid #d9e0d9;border-radius:8px;'
        f'padding:12px 14px;height:100%;">'
        f'<h4 style="color:#0e7c39;margin:0 0 4px 0;">{icon} {sucursal} '
        f'<span style="font-weight:400;color:#888;font-size:12px;">({alias})</span></h4>'
        f'{cobranzas_html}{cierre_html}{choco_html}'
        f'</div>'
    )


def _asistentes_section_html(today_iso: str) -> str:
    """Sección de asistentes Guayaquil/Quito lado a lado (2 columnas)."""
    state = activity_state.load()
    asistentes_con_data = [
        e for e in ASISTENTE_EMAILS
        if e in state.get("users", {})
    ]
    if not asistentes_con_data:
        return ""

    # Si solo hay uno, mostrar de ancho completo; si dos, en columnas.
    # Asistente de cada sucursal (GYE/UIO) desde core_config — sin hardcode de email.
    info_email = next(
        (e for e in ASISTENTE_EMAILS if core_config.sucursal_for(e) == "GYE"), ""
    )
    quito_email = next(
        (e for e in ASISTENTE_EMAILS if core_config.sucursal_for(e) == "UIO"), ""
    )
    info_html = (
        _asistente_column_html(info_email, today_iso)
        if info_email in asistentes_con_data
        else '<div style="color:#999;font-style:italic;padding:12px;">Guayaquil: sin actividad</div>'
    )
    quito_html = (
        _asistente_column_html(quito_email, today_iso)
        if quito_email in asistentes_con_data
        else '<div style="color:#999;font-style:italic;padding:12px;">Quito: sin actividad</div>'
    )

    return (
        '<h3 style="color:#0e7c39;margin-top:24px;margin-bottom:6px;">'
        '🏪 Sucursales (asistentes)</h3>'
        '<table style="width:100%;border-collapse:separate;border-spacing:8px 0;">'
        '<tr>'
        f'<td style="width:50%;vertical-align:top;">{info_html}</td>'
        f'<td style="width:50%;vertical-align:top;">{quito_html}</td>'
        '</tr></table>'
    )


def _proyectos_pendientes_section(collaborator_data: list[dict]) -> str:
    """Lista compacta de proyectos semanales sin avance hoy (Mateo/gsanchez)."""
    rows = ""
    for d in collaborator_data:
        if d["email"] in ASISTENTE_EMAILS:
            continue
        sin_avance = d.get("semanales_sin_avance", [])
        for p in sin_avance:
            prio_badge = {"alta": "🔴", "media": "🟡", "baja": "⚪"}.get(p.get("priority", "media"), "")
            rows += (
                f'<tr>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;color:#555;font-weight:600;font-size:12px;">{d["alias"]}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;font-size:12px;">{prio_badge} {p["nombre"]}</td>'
                f'<td style="padding:4px 8px;border-bottom:1px solid #f0f0f0;text-align:right;font-size:12px;color:#666;">{p["avance"]:.0f}%</td>'
                f'</tr>'
            )
    if not rows:
        return ""
    return (
        '<h3 style="color:#ef6c00;margin-top:24px;margin-bottom:6px;">'
        '📋 Proyectos semanales en curso sin avance hoy</h3>'
        '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
        '<thead><tr style="background:#fff8e1;color:#ef6c00;">'
        '<th style="text-align:left;padding:4px 8px;">Colaborador</th>'
        '<th style="text-align:left;padding:4px 8px;">Proyecto</th>'
        '<th style="text-align:right;padding:4px 8px;">Avance</th>'
        '</tr></thead>'
        f'<tbody>{rows}</tbody></table>'
    )


def _consolidated_daily_summary_html(
    collaborator_emails: list[str], target_date: date | None = None
) -> str:
    """Arma el HTML consolidado v2 (Phase O.2, 2026-06-05).

    Estructura: resumen ejecutivo arriba + sección problemáticas + asistentes en
    2 columnas + proyectos pendientes. Mucho más compacto y accionable.

    `target_date` (2026-06-15): si se pasa, arma el resumen para ESA fecha (con
    su semana ISO) en vez de hoy — lo usa el recap del sábado que corre el
    lunes 8 AM. Si es None, comportamiento histórico (hoy EC).
    """
    hoy = target_date or _hoy_ec()
    today_iso = hoy.isoformat()
    wk_target = activity_state.week_key(hoy)
    es_sabado_recap = target_date is not None and hoy.weekday() == 5
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses_es = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
                "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    fecha_humana = f"{dias_es[hoy.weekday()]} {hoy.day} de {meses_es[hoy.month-1]} de {hoy.year}"

    sorted_collabs = sorted(
        collaborator_emails, key=lambda e: e.split("@")[0].lower()
    )

    # Recopilar data por colaborador (asistentes + no-asistentes igual van al
    # resumen ejecutivo arriba)
    collaborator_data: list[dict] = []
    for email in sorted_collabs:
        alias = email.split("@")[0]
        classif = _classify_dailies(email, today_iso, wk=wk_target)
        # ¿Asistente GYE ausente por turno rotativo este sábado? (no es error)
        ausente_rotativo = (
            es_sabado_recap
            and email.lower() in GYE_ROTATIVOS_SABADO
            and _gye_sin_reporte_dia(email, today_iso)
        )
        # Horario
        horario = activity_state.get_day_schedule(email, today_iso)
        if ausente_rotativo:
            hora = '<span style="color:#888;">ausencia esperada (turno)</span>'
        elif horario is None:
            hora = '<span style="color:#999;">sin reportar</span>'
        elif horario.get("estandar"):
            hora = f"{activity_state.horario_estandar_corto(today_iso)} (std)"
        else:
            desde = horario.get("desde") or "?"
            hasta = horario.get("hasta") or "?"
            hora = f"{desde}–{hasta}"
        # Cierre caja text para tabla resumen
        cierre = activity_state.get_cierre_caja(email, today_iso)
        if cierre:
            cierre_html = f'${cierre["entregado"]:,.0f}'
        elif ausente_rotativo:
            cierre_html = "—"
        elif email in ASISTENTE_EMAILS:
            cierre_html = '<span style="color:#c62828;">sin marcar</span>'
        else:
            cierre_html = "—"
        collaborator_data.append({
            "email": email,
            "alias": alias,
            "hora": hora,
            "counts": classif["counts"],
            "problematicas": classif["problematicas"],
            "semanales_sin_avance": classif["semanales_sin_avance"],
            "cierre_html": cierre_html,
        })

    if not collaborator_data:
        body_html = (
            '<p style="color:#999;font-style:italic;">'
            '(Ningún colaborador tiene actividades hoy.)</p>'
        )
    else:
        # Phase T (2026-06-09): rediseño por BLOQUES por colaborador.
        # Quita la tabla ejecutiva (muy compacta, no decía nada). Cada bloque
        # tiene: header colorido, horario destacado, actividades, cierre completo
        # con denominaciones (para asistentes), chocolates.

        # Phase V (2026-06-11): orden por ciudad — gerencial → GYE → UIO.
        #   1. Gabriela Sánchez (gerente comercial)
        #   2. Mateo Alvarado (comercial)
        #   3. GYE: Asistente 1 (info@)
        #   4. GYE: Asistente 2 (José — bloque dedicado entre info@ y quito@)
        #   5. UIO: Asistente 1 (quito@)
        _ROLE_RANK = {"gerente_comercial": 0, "analista": 1, "asistente": 2}

        def _orden(d: dict) -> int:
            email = d["email"].lower()
            base = _ROLE_RANK.get(core_config.role_for(email), 99)
            if base == 2:  # asistentes: GYE (2) antes que UIO (4); José va entre medio
                return 2 if core_config.sucursal_for(email) == "GYE" else 4
            return base

        sorted_data = sorted(collaborator_data, key=_orden)

        # Sección "Lo que requiere seguimiento" arriba (problemas)
        problemas = _problemas_section(collaborator_data)
        # Phase V (2026-06-11): nueva sección — colaboradores que NO marcaron NADA.
        # En el recap del sábado NO aplica: los asistentes GYE pueden estar
        # ausentes legítimamente por el turno rotativo (no es "no llenaron").
        alerta_sin_marcar = (
            "" if es_sabado_recap else _sin_marcar_section(collaborator_data)
        )

        # Phase V (2026-06-11): bloques individuales, con José GYE intercalado
        # después de info@ (Asistente 1 GYE) y antes de quito@ (UIO).
        bloques_list: list[str] = []
        jose_insertado = False
        for d in sorted_data:
            email_l = d["email"].lower()
            bloques_list.append(_collaborator_block_html_v2(d["email"], target_date=target_date))
            # Después del Asistente 1 de la sucursal del chofer, insertar su bloque
            if email_l == GYE_ASISTENTE1_EMAIL and not jose_insertado:
                try:
                    jose_block = _jose_consolidated_block_html(today_iso)
                    bloques_list.append(jose_block)
                except Exception:
                    pass
                jose_insertado = True
        # Si info@ no estaba en sorted_data (sin state), igual sumar José
        # al final como antes (fallback)
        if not jose_insertado:
            try:
                bloques_list.append(_jose_consolidated_block_html(today_iso))
            except Exception:
                pass

        blocks_html = "".join(bloques_list)
        body_html = alerta_sin_marcar + problemas + blocks_html

    if es_sabado_recap:
        titulo_html = f"Resumen del sábado — {fecha_humana}"
        intro_html = (
            "Hola Daniel y Gabriela, acá el resumen de las actividades del "
            "<b>sábado</b>. En GYE el turno de sábado es rotativo entre los dos "
            "asistentes: si uno no reportó, es ausencia esperada (no pendiente)."
        )
    else:
        titulo_html = f"Resumen diario del equipo — {fecha_humana}"
        intro_html = (
            "Hola Daniel y Gabriela, acá el resumen del día. Foco en lo que "
            "requiere seguimiento (rojo/ámbar)."
        )

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><style>
body {{ font-family:'Segoe UI',Arial,sans-serif; color:#2c2c2c;
       max-width:800px; margin:0; padding:18px; background:#fafafa; }}
h2 {{ color:#0e7c39; border-bottom:2px solid #0e7c39; padding-bottom:8px;
      margin-top:0; }}
h3 {{ margin-bottom:6px; }}
.footer {{ font-size:11px; color:#888; margin-top:24px; border-top:1px solid #eee;
            padding-top:10px; }}
</style></head><body>

<h2>{titulo_html}</h2>

<p style="font-size:13px;color:#555;">
{intro_html}
</p>

<h3 style="color:#0e7c39;margin-top:18px;">📊 Resumen ejecutivo</h3>

{body_html}

<div class='footer'>
🔴 Alta · 🟡 Media · ⚪ Baja · ✅ Hechas · ⚠️ Parcial · ❌ No hechas · ⏳ Sin marcar.<br>
Resumen consolidado automático del Activities Bot · {hoy.strftime("%d/%m/%Y")}
{datetime.now(LOCAL_TZ).strftime(" %H:%M")} (hora Ecuador).
</div>
</body></html>
"""


def _send_consolidated_daily_summary(
    to_override: list[str] | None = None,
    cc_override: list[str] | None = None,
    target_date: date | None = None,
) -> dict:
    """Manda el correo consolidado con todos los colaboradores no-supervisor.

    To/CC: parámetros explícitos (testing) > env CONSOLIDATED_DAILY_TO/_CC >
    defaults. Fase 2 (auditoría A8): los overrides de testing ya NO mutan
    os.environ del proceso.

    `target_date` (2026-06-15): si se pasa, el resumen cubre ESA fecha en vez
    de hoy — lo usa el recap del sábado que corre el lunes 8 AM.
    """
    # Tomar todos los users del state que NO son supervisores
    state = activity_state.load()
    all_users = set((state.get("users") or {}).keys())
    # Phase V (2026-06-10): José se incluye SOLO como bloque al final del
    # consolidado (no como colaborador normal — su state es de logística,
    # no de actividades).
    # Los emails aislados `unidentified-*` (cuando _user_email del bot no pudo
    # resolver quién escribió) NUNCA van en el consolidado — son pseudo-users.
    collaborators = sorted(
        u for u in all_users
        if u.lower() not in SUPERVISORS_ONLY_EMAILS
        and u.lower() != JOSE_EMAIL_CONS
        and not u.lower().startswith("unidentified-")
    )

    html = _consolidated_daily_summary_html(collaborators, target_date=target_date)

    sender = TRACKER_EMAIL_FROM  # malvarado@ (siempre desde el mismo)
    to_str = os.environ.get("CONSOLIDATED_DAILY_TO", CONSOLIDATED_DAILY_TO_DEFAULT)
    cc_str = os.environ.get("CONSOLIDATED_DAILY_CC", CONSOLIDATED_DAILY_CC_DEFAULT)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_str.split(",") if e.strip()]
    if to_override:
        to_list = [e.strip() for e in to_override if e.strip()]
    if cc_override is not None:  # lista vacía = sin CC (testing)
        cc_list = [e.strip() for e in cc_override if e.strip()]

    es_sabado_recap = target_date is not None and target_date.weekday() == 5
    fecha_str = (target_date or _hoy_ec()).strftime("%d/%m/%Y")
    if es_sabado_recap:
        subject = f"Resumen del sábado — {fecha_str}"
    else:
        subject = f"Resumen diario del equipo — {fecha_str}"
    graph_mail.send(
        from_user=sender,
        to=to_list,
        subject=subject,
        html_body=html,
        cc=cc_list,
    )
    return {
        "ok": True,
        "from": sender,
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
        "collaborators": collaborators,
        "target_date": (target_date or _hoy_ec()).isoformat(),
    }


def send_saturday_recap_summary(
    to_override: list[str] | None = None,
    cc_override: list[str] | None = None,
) -> dict:
    """Recap del sábado que corre el lunes 8 AM. Reporta el sábado anterior
    (única vista consolidada del sábado: el job 18:30 es Lun-Vie y nunca lo
    cubría). Reutiliza toda la maquinaria del consolidado con target_date."""
    sabado = _ultimo_sabado()
    return _send_consolidated_daily_summary(
        to_override, cc_override, target_date=sabado
    )


# ===== Resumen de carga por colaborador (Feature 2026-06-15) =====
def _team_collaborator_emails() -> list[str]:
    """Colaboradores no-supervisor con state (excluye José y unidentified)."""
    state = activity_state.load()
    return sorted(
        u for u in (state.get("users") or {})
        if u.lower() not in SUPERVISORS_ONLY_EMAILS
        and u.lower() != JOSE_EMAIL_CONS
        and not u.lower().startswith("unidentified-")
    )


def _workload_rollup(user_email: str | None) -> dict:
    """Carga de UN colaborador: cuenta tareas no-diarias por estado efectivo y
    lista las próximas fechas (no finalizadas). Las diarias no cuentan acá."""
    email = (user_email or "").lower()
    tasks = activity_state.list_tasks(email)
    counts = {"pendiente": 0, "en_progreso": 0, "finalizada": 0, "vencida": 0}
    proximas: list[dict] = []
    for aid, entry, eff in tasks:
        if eff in counts:
            counts[eff] += 1
        if eff != "finalizada" and entry.get("fecha_limite"):
            proximas.append({
                "nombre": entry.get("nombre", aid),
                "fecha_limite": entry["fecha_limite"],
                "status": eff,
            })
    proximas.sort(key=lambda p: p["fecha_limite"])
    return {
        "email": email,
        "nombre": EMAIL_TO_NAME.get(email, user_email),
        "pendientes": counts["pendiente"],
        "en_progreso": counts["en_progreso"],
        "finalizadas": counts["finalizada"],
        "vencidas": counts["vencida"],
        "abiertas": counts["pendiente"] + counts["en_progreso"] + counts["vencida"],
        "proximas": proximas,
    }


def _team_workload_html() -> str:
    """HTML del roll-up de carga de TODO el equipo (tabla resumen + próximas
    fechas por colaborador). Lo usa el reporte semanal y el endpoint admin."""
    emails = _team_collaborator_emails()
    css = (
        "<style>body{font-family:Segoe UI,Arial,sans-serif;color:#222;}"
        "table{border-collapse:collapse;width:100%;margin:6px 0 18px;}"
        "th,td{border:1px solid #ddd;padding:6px 8px;font-size:13px;text-align:left;}"
        "th{background:#0e7c39;color:#fff;}h3{margin:18px 0 4px;}"
        ".v{color:#c62828;font-weight:bold;}</style>"
    )
    hoy = _hoy_ec().strftime("%d/%m/%Y")
    parts = [css, f"<h2>📋 Carga de tareas del equipo — {hoy}</h2>"]
    if not emails:
        parts.append("<p>No hay colaboradores con tareas registradas.</p>")
        return "".join(parts)
    parts.append(
        "<table><tr><th>Colaborador</th><th>Pendientes</th><th>En progreso</th>"
        "<th>Vencidas</th><th>Finalizadas (semana)</th></tr>"
    )
    rollups = [_workload_rollup(e) for e in emails]
    for r in rollups:
        venc = f'<span class="v">{r["vencidas"]}</span>' if r["vencidas"] else "0"
        parts.append(
            f'<tr><td>{r["nombre"]}</td><td>{r["pendientes"]}</td>'
            f'<td>{r["en_progreso"]}</td><td>{venc}</td>'
            f'<td>{r["finalizadas"]}</td></tr>'
        )
    parts.append("</table>")
    for r in rollups:
        if not r["proximas"]:
            continue
        parts.append(
            f'<h3>{r["nombre"]} — próximas fechas</h3><table>'
            "<tr><th>Tarea</th><th>Fecha límite</th><th>Estado</th></tr>"
        )
        for p in r["proximas"]:
            cls = ' class="v"' if p["status"] == "vencida" else ""
            parts.append(
                f'<tr><td>{p["nombre"]}</td><td{cls}>{p["fecha_limite"]}</td>'
                f'<td>{p["status"]}</td></tr>'
            )
        parts.append("</table>")
    return "".join(parts)


def _workload_text_for_chat(user_email: str | None = None) -> str:
    """Markdown compacto para el comando del bot. user_email None = todo el
    equipo; con valor = solo ese colaborador."""
    targets = [user_email.lower()] if user_email else _team_collaborator_emails()
    if not targets:
        return "No hay colaboradores con tareas registradas."
    lines = ["📋 **Carga de tareas**"]
    for email in targets:
        r = _workload_rollup(email)
        lines.append(
            f"\n**{r['nombre']}** — "
            f"⏳ {r['pendientes']} pend · 🔄 {r['en_progreso']} en progreso · "
            f"⚠️ {r['vencidas']} vencidas · ✅ {r['finalizadas']} finalizadas"
        )
        for p in r["proximas"][:5]:
            marca = "⚠️" if p["status"] == "vencida" else "•"
            lines.append(f"  {marca} {p['nombre']} → {p['fecha_limite']} ({p['status']})")
    return "\n".join(lines)


def send_team_workload_summary(
    to_override: list[str] | None = None,
    cc_override: list[str] | None = None,
) -> dict:
    """Envía el roll-up de carga del equipo a supervisores (Daniel + Gabriela).

    Lo llama el job semanal del viernes (tras los resúmenes per-user) y el
    endpoint admin de testing. Reusa los destinatarios del consolidado diario.
    """
    html = _team_workload_html()
    sender = TRACKER_EMAIL_FROM
    to_str = os.environ.get("CONSOLIDATED_DAILY_TO", CONSOLIDATED_DAILY_TO_DEFAULT)
    cc_str = os.environ.get("CONSOLIDATED_DAILY_CC", CONSOLIDATED_DAILY_CC_DEFAULT)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_str.split(",") if e.strip()]
    if to_override:
        to_list = [e.strip() for e in to_override if e.strip()]
    if cc_override is not None:
        cc_list = [e.strip() for e in cc_override if e.strip()]
    subject = f"Resumen de carga del equipo — semana {activity_state.week_key()}"
    graph_mail.send(
        from_user=sender, to=to_list, subject=subject, html_body=html, cc=cc_list,
    )
    return {"ok": True, "from": sender, "to": to_list, "cc": cc_list, "subject": subject}


def _send_confirmacion_cierre_email(
    emisor_email: str,
    fecha: str,
    sucursal: str,
    validador_email: str,
    estado: str,
    monto_recibido: float | None,
    razon: str,
    entregado_reportado: float,
) -> dict:
    """Phase V (2026-06-11): DESHABILITADO. Mateo pidió quitar el correo de
    'Recepción CONFIRMADA' porque el cierre de caja es solo del efectivo (no
    de ventas del día). Gabriela Sánchez no debe recibir nada de este flujo.

    Se mantiene la función para preservar llamadas existentes — es no-op."""
    import logging as _lg
    _lg.getLogger(__name__).info(
        "_send_confirmacion_cierre_email DESHABILITADO (Phase V): emisor=%s "
        "sucursal=%s estado=%s", emisor_email, sucursal, estado,
    )
    return {"ok": True, "skipped": True, "reason": "deshabilitado_phase_v"}

    # === código histórico preservado pero inalcanzable ===
    """Manda email cuando el validador (Daniel/Gabriela) confirma recepción
    o discrepancia del cierre de caja. Va a Mateo+Gabriela+emisor.

    Phase P (2026-06-05).
    """
    fecha_humana = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    validador_alias = validador_email.split("@")[0]
    emisor_alias = emisor_email.split("@")[0]

    if estado == "confirmado":
        emoji = "✅"
        titulo = f"Recepción CONFIRMADA — {sucursal} {fecha_humana}"
        cuerpo_extra = (
            f"<p><b>{validador_alias}</b> confirmó la recepción exacta de "
            f"<b>${entregado_reportado:,.2f}</b> entregados por <b>{emisor_alias}</b>.</p>"
        )
        color = "#0e7c39"
    elif estado == "discrepancia":
        emoji = "⚠️"
        diff = (monto_recibido or 0) - entregado_reportado
        sign = "+" if diff > 0 else ""
        titulo = f"DISCREPANCIA en cierre — {sucursal} {fecha_humana}"
        cuerpo_extra = (
            f"<p style='color:#c62828;'>"
            f"<b>{validador_alias}</b> reporta una <b>discrepancia</b> en la recepción:"
            f"</p>"
            f"<ul>"
            f"<li>Reportado por {emisor_alias}: <b>${entregado_reportado:,.2f}</b></li>"
            f"<li>Recibido realmente: <b>${monto_recibido:,.2f}</b></li>"
            f"<li>Diferencia: <b>{sign}${diff:,.2f}</b></li>"
            f"</ul>"
            f"<p><b>Razón:</b> <i>{razon or '(sin detalle)'}</i></p>"
        )
        color = "#c62828"
    else:  # no_recibido
        emoji = "📝"
        titulo = f"Pendiente de recepción — {sucursal} {fecha_humana}"
        cuerpo_extra = (
            f"<p><b>{validador_alias}</b> marcó como pendiente la recepción de "
            f"<b>${entregado_reportado:,.2f}</b> reportados por <b>{emisor_alias}</b>.</p>"
            f"<p><b>Razón:</b> <i>{razon or '(sin detalle)'}</i></p>"
        )
        color = "#ef6c00"

    html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><style>
body {{ font-family:'Segoe UI',Arial,sans-serif; color:#2c2c2c;
       max-width:620px; margin:0; padding:18px; }}
h2 {{ color:{color}; border-bottom:2px solid {color}; padding-bottom:8px;
      margin-top:0; }}
.footer {{ font-size:11px; color:#888; margin-top:24px; border-top:1px solid #eee;
            padding-top:8px; }}
</style></head><body>
<h2>{emoji} {titulo}</h2>
{cuerpo_extra}
<div class='footer'>
Confirmación registrada el {datetime.now(LOCAL_TZ).strftime("%d/%m/%Y %H:%M")}
(hora Ecuador). Sistema Activities Bot.
</div>
</body></html>"""

    # Destinatarios: gerencia (JEFE) + emisor + CC analista (MIO)
    to_default = ",".join(core_config.JEFE)
    to_str = os.environ.get("CONFIRMACION_CIERRE_TO", to_default)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    if emisor_email not in to_list:
        to_list.append(emisor_email)
    cc_list = [core_config.MIO]

    subject = f"{emoji} {titulo}"
    graph_mail.send(
        from_user=TRACKER_EMAIL_FROM,
        to=to_list,
        subject=subject,
        html_body=html,
        cc=cc_list,
    )
    return {"ok": True, "to": to_list, "cc": cc_list, "subject": subject}


def _build_fondo_caja_html(
    rec: dict, sucursal: str, tipo: str, emisor_email: str, fecha: str
) -> str:
    """HTML compartido para apertura y cierre de caja (Phase S 2026-06-08).

    Muestra: detalle por denominación + total contado vs fondo esperado +
    status (cuadra / sobra / falta).
    """
    fecha_humana = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    titulo_tipo = "Apertura" if tipo == "apertura" else "Cierre"
    icono = "☀️" if tipo == "apertura" else "🌙"

    total = rec.get("total", 0)
    fondo_esp = rec.get("fondo_esperado", rec.get("fondo", 0))
    diferencia = rec.get("diferencia", 0)
    status = rec.get("status", "cuadra")

    # Color del status
    if status == "cuadra":
        status_color = "#2e7d32"
        status_text = f"✅ Cuadra perfecto (contado coincide con fondo de ${fondo_esp:,.2f})"
    elif status == "sobra":
        status_color = "#ef6c00"
        status_text = f"⚠️ Sobra ${diferencia:,.2f} (contado ${total:,.2f} vs fondo ${fondo_esp:,.2f})"
    else:  # falta
        status_color = "#c62828"
        status_text = f"🔴 Falta ${abs(diferencia):,.2f} (contado ${total:,.2f} vs fondo ${fondo_esp:,.2f})"

    # Reconstruir detalle si no viene
    detalle_billetes = rec.get("detalle_billetes")
    detalle_monedas = rec.get("detalle_monedas")
    if not detalle_billetes or not detalle_monedas:
        calc = activity_state.calcular_cierre_caja(rec.get("denoms", {}), sucursal=sucursal)
        detalle_billetes = calc["detalle_billetes"]
        detalle_monedas = calc["detalle_monedas"]

    def _row(d: dict) -> str:
        return (
            f"<tr><td style='padding:5px 10px;border-bottom:1px solid #eee;'>"
            f"{d['label']} × {d['cantidad']}</td>"
            f"<td style='padding:5px 10px;border-bottom:1px solid #eee;text-align:right;"
            f"font-family:Consolas,monospace;'>${d['subtotal']:,.2f}</td></tr>"
        )

    rows_b = "".join(_row(d) for d in detalle_billetes if d["cantidad"] > 0)
    rows_m = "".join(_row(d) for d in detalle_monedas if d["cantidad"] > 0)
    if not rows_b:
        rows_b = "<tr><td colspan='2' style='padding:5px 10px;color:#999;'>(sin billetes)</td></tr>"
    if not rows_m:
        rows_m = "<tr><td colspan='2' style='padding:5px 10px;color:#999;'>(sin monedas)</td></tr>"

    hora_marca = (rec.get("marcado_at") or "").split("T")[-1][:5] or "—"
    notas_html = (
        f"<p style='font-size:13px;'><b>Notas:</b> {rec.get('notas') or '<i>(sin notas)</i>'}</p>"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'><style>
body {{ font-family:'Segoe UI',Arial,sans-serif; color:#2c2c2c; max-width:680px;
       margin:0; padding:18px; }}
h3 {{ color:{status_color}; margin-top:0; border-bottom:2px solid {status_color};
      padding-bottom:8px; }}
table {{ border-collapse:collapse; font-size:13px; width:100%; }}
.status-box {{ background:#f4faf6; border:2px solid {status_color}; border-radius:6px;
               padding:14px 18px; margin:14px 0; }}
.footer {{ font-size:11px; color:#888; margin-top:24px; border-top:1px solid #eee;
            padding-top:8px; }}
</style></head><body>

<h3>{icono} {titulo_tipo} de caja {sucursal} – {fecha_humana}</h3>

<p>
  <b>Sucursal:</b> {sucursal}<br>
  <b>Responsable:</b> {emisor_email}<br>
  <b>Hora del conteo:</b> {hora_marca}
</p>

<div class='status-box'>
  <div style='font-size:18px;color:{status_color};font-weight:700;'>{status_text}</div>
</div>

<table>
  <thead>
    <tr>
      <th style='background:#0e7c39;color:white;padding:8px 10px;text-align:left;'>Conteo</th>
      <th style='background:#0e7c39;color:white;padding:8px 10px;text-align:right;'>Valor</th>
    </tr>
  </thead>
  <tbody>
    <tr><td colspan='2' style='background:#f4faf6;font-weight:600;color:#0e7c39;padding:6px 10px;'>Billetes</td></tr>
    {rows_b}
    <tr><td colspan='2' style='background:#f4faf6;font-weight:600;color:#0e7c39;padding:6px 10px;'>Monedas</td></tr>
    {rows_m}
    <tr>
      <td style='padding:8px 10px;font-weight:700;border-top:2px solid #0e7c39;'>TOTAL CONTADO</td>
      <td style='padding:8px 10px;text-align:right;font-weight:700;font-family:Consolas,monospace;border-top:2px solid #0e7c39;'>${total:,.2f}</td>
    </tr>
    <tr>
      <td style='padding:5px 10px;color:#666;'>Fondo objetivo</td>
      <td style='padding:5px 10px;text-align:right;color:#666;font-family:Consolas,monospace;'>${fondo_esp:,.2f}</td>
    </tr>
    <tr>
      <td style='padding:5px 10px;font-weight:600;color:{status_color};'>Diferencia</td>
      <td style='padding:5px 10px;text-align:right;font-weight:600;color:{status_color};font-family:Consolas,monospace;'>${diferencia:+,.2f}</td>
    </tr>
  </tbody>
</table>

{notas_html}

<div class='footer'>
{titulo_tipo} marcado automáticamente por el Activities Bot el {fecha_humana} a las {hora_marca}.
Las ventas del día se manejan aparte (no se cuentan acá).
</div>

</body></html>
"""


def _send_apertura_caja_email(
    emisor_email: str, fecha: str, sucursal: str
) -> dict:
    """Phase S: email al equipo cuando info@/quito@ registra apertura de caja."""
    rec = activity_state.get_apertura_caja(emisor_email, fecha)
    if not rec:
        raise RuntimeError(f"No hay apertura para {emisor_email} en {fecha}")

    candidate = (emisor_email or "").strip().lower()
    sender = candidate if "@" in candidate else TRACKER_EMAIL_FROM
    to_str = os.environ.get("APERTURA_CAJA_EMAIL_TO", CIERRE_CAJA_TO_DEFAULT)
    cc_str = os.environ.get("APERTURA_CAJA_EMAIL_CC", CIERRE_CAJA_CC_DEFAULT)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_str.split(",") if e.strip()]

    html = _build_fondo_caja_html(rec, sucursal, "apertura", emisor_email, fecha)
    fecha_str = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    subject = f"☀️ Apertura de caja {sucursal} – {fecha_str}"

    graph_mail.send(
        from_user=sender,
        to=to_list,
        subject=subject,
        html_body=html,
        cc=cc_list,
    )
    return {"ok": True, "from": sender, "to": to_list, "cc": cc_list, "subject": subject}


def _send_cierre_caja_email(
    user_email: str, fecha: str, sucursal: str
) -> dict:
    """Envía el correo de cierre de caja del día a Daniel + Gabriela CC Mateo.

    `to` y `cc` se pueden override con env vars CIERRE_CAJA_EMAIL_TO y
    CIERRE_CAJA_EMAIL_CC.
    """
    rec = activity_state.get_cierre_caja(user_email, fecha)
    if not rec:
        raise RuntimeError(
            f"No hay cierre de caja guardado para {user_email} en {fecha}"
        )

    candidate = (user_email or "").strip().lower()
    sender = candidate if "@" in candidate else TRACKER_EMAIL_FROM

    to_str = os.environ.get("CIERRE_CAJA_EMAIL_TO", CIERRE_CAJA_TO_DEFAULT)
    cc_str = os.environ.get("CIERRE_CAJA_EMAIL_CC", CIERRE_CAJA_CC_DEFAULT)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    cc_list = [e.strip() for e in cc_str.split(",") if e.strip()]

    # Phase S (2026-06-08): usar helper compartido con apertura — formato
    # nuevo con status "cuadra/sobra/falta"
    html = _build_fondo_caja_html(rec, sucursal, "cierre", user_email, fecha)
    fecha_str = datetime.strptime(fecha, "%Y-%m-%d").strftime("%d/%m/%Y")
    subject = f"🌙 Cierre de caja {sucursal} – {fecha_str}"

    graph_mail.send(
        from_user=sender,
        to=to_list,
        subject=subject,
        html_body=html,
        cc=cc_list,
    )
    return {
        "ok": True,
        "from": sender,
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
    }

