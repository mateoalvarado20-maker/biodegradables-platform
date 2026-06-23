"""Agente conversacional sobre Contifico + HubSpot.

Usa la API de Anthropic con tool use. Claude llama las herramientas (Contifico
para ventas/clientes/vendedores, HubSpot para leads/deals), y arma una
respuesta en lenguaje natural.

Es el "cerebro" del bot de Teams (`teams_bot.py`) — se importa y se llama
`ask(question)`.

Uso CLI (debug):
    python ask_agent.py "cuánto vendimos hoy?"
    python ask_agent.py "quién vendió más ayer?"
    python ask_agent.py --verbose "cómo va el cumplimiento del mes?"

Phase B (2026-05-29):
- Removidas las tools de Power BI (DAX queries). Reemplazadas por Contifico.
- Daniel ya no necesita configurar Service Principal en PBI.
- daily_report.py sigue usando PBI (no se toca), pero el bot va directo a Contifico.

Phase C (2026-05-29):
- Tools de tracker: list_today_activities, mark_daily_activity, mark_weekly_progress,
  send_daily_summary_email. El bot ahora dispara check-ins diarios y manda el resumen
  a Daniel + Gabriela cuando el user lo confirma o naturalmente cierra el día.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from anthropic import Anthropic

import activity_state
import contifico_client
import core_config
import forecasting
import graph_calendar_app
import graph_mail
import hubspot_client
import news_brief
import reminders

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000
MAX_ITERATIONS = 10
LOCAL_TZ = timezone(timedelta(hours=-5))

logger = logging.getLogger("ask_agent")


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



def _parse_date(s: str | None, default: date | None = None) -> date:
    """Parsea fecha ISO YYYY-MM-DD. Si está vacía, usa default o hoy."""
    if not s:
        return default or _hoy_ec()
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return default or _hoy_ec()


# ============ Tracker helpers (Phase C) ============
TRACKER_EMAIL_FROM = os.environ.get(
    "TRACKER_TARGET_USER", "malvarado@biodegradablesecuador.com"
).strip()
TRACKER_EMAIL_TO_DEFAULT = (
    "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com"
)

# Directorio de colaboradores: alias → email. Lo lee el Data Bot para que la
# gerencia diga "Mateo" en vez de "malvarado@..." cuando asigna tareas.
# Editable via env var KNOWN_COLLABORATORS="alias1:email1,alias2:email2".
def _load_collaborators() -> dict[str, str]:
    raw = os.environ.get(
        "KNOWN_COLLABORATORS",
        "mateo:malvarado@biodegradablesecuador.com",
    )
    out: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if ":" in entry:
            alias, email = entry.split(":", 1)
            out[alias.strip().lower()] = email.strip().lower()
    return out


COLLABORATORS = _load_collaborators()


def _strip_accents_lower(s: str) -> str:
    """Minúsculas + sin acentos (NFKD). 'Sánchez' → 'sanchez'."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _norm_name(s: str) -> str:
    """Normaliza un nombre para matching: sin acentos, sin paréntesis (roles),
    puntuación → espacio, espacios colapsados.

    'Gabriela Bravo (Asistente 1 GYE)' → 'gabriela bravo'
    'GABRIELA  SÁNCHEZ.' → 'gabriela sanchez'
    """
    s = _strip_accents_lower(s)
    # Quitar lo que esté entre paréntesis (roles, sucursales)
    out: list[str] = []
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    s = "".join(out)
    # No alfanumérico → espacio (incluye '@' y '.', para tokens de email)
    s = "".join(ch if ch.isalnum() else " " for ch in s)
    return " ".join(s.split())


def _collaborator_directory() -> tuple[dict[str, set[str]], dict[str, set[str]], set[str]]:
    """Construye el índice de búsqueda de colaboradores REGISTRADOS.

    Solo se delega a emails presentes en KNOWN_COLLABORATORS (garantía A6/C4:
    nunca a un email arbitrario). EMAIL_TO_NAME solo aporta el nombre humano
    de esos emails registrados; un email que aparece en EMAIL_TO_NAME pero NO
    en KNOWN_COLLABORATORS (ej. dsanchez@ supervisor) NO es destino válido.

    Devuelve:
      exact: término_normalizado → {emails}  (alias, local-part, nombre completo)
      tokens: email → {tokens del nombre/alias/local-part}  (para match parcial)
      registered: set de emails registrados (lower)
    """
    registered = {e.strip().lower() for e in COLLABORATORS.values() if e}
    exact: dict[str, set[str]] = {}
    tokens: dict[str, set[str]] = {e: set() for e in registered}

    def _add_exact(term: str, email: str) -> None:
        nt = _norm_name(term)
        if nt:
            exact.setdefault(nt, set()).add(email)
            tokens[email].update(nt.split())

    # Aliases de KNOWN_COLLABORATORS (incluye 'gabriela'→gsanchez@, 'gye'→info@)
    for alias, email in COLLABORATORS.items():
        email = email.strip().lower()
        if email in registered:
            _add_exact(alias, email)
    # Local-part del email ('gsanchez', 'malvarado') + nombre humano completo
    for email in registered:
        _add_exact(email.split("@")[0], email)
        human = EMAIL_TO_NAME.get(email)
        if human:
            _add_exact(human, email)
    return exact, tokens, registered


def _resolve_collaborator_detail(name_or_email: str) -> dict:
    """Resuelve un nombre/email a un colaborador registrado, con diagnóstico.

    Robusto a: acentos, mayúsculas, espacios extra, nombre completo
    ('Gabriela Sánchez'), alias ('gabriela'), local-part ('gsanchez') y email
    completo. Si un término identifica a >1 colaborador, devuelve 'ambiguous'
    con los candidatos en vez de elegir uno al azar (evita asignar a la
    Gabriela equivocada).

    Returns dict: {status: 'ok'|'ambiguous'|'not_found', email, candidates}
      - candidates: lista de {email, nombre} cuando ambiguous/not_found.
    """
    exact, tokens, registered = _collaborator_directory()

    def _cands(emails) -> list[dict]:
        return [
            {"email": e, "nombre": EMAIL_TO_NAME.get(e, e)}
            for e in sorted(emails)
        ]

    def _all() -> list[dict]:
        return _cands(registered)

    if not name_or_email or not str(name_or_email).strip():
        return {"status": "not_found", "email": None, "candidates": _all()}

    raw = str(name_or_email).strip()

    # 1. Email explícito: debe estar registrado (preserva A6/C4 — no fantasmas).
    if "@" in raw:
        low = raw.lower()
        if low in registered:
            return {"status": "ok", "email": low, "candidates": []}
        # ¿el local-part o el nombre matchea aunque el dominio esté mal tipeado?
        # NO asumimos: lo tratamos como término más abajo solo si no tiene
        # dominio externo distinto. Para un email completo no registrado,
        # rechazamos explícito.
        return {"status": "not_found", "email": None, "candidates": _all()}

    nq = _norm_name(raw)
    if not nq:
        return {"status": "not_found", "email": None, "candidates": _all()}

    # 2. Match exacto por término normalizado (alias / nombre completo / local-part)
    if nq in exact:
        emails = exact[nq]
        if len(emails) == 1:
            return {"status": "ok", "email": next(iter(emails)), "candidates": []}
        return {"status": "ambiguous", "email": None, "candidates": _cands(emails)}

    # 3. Match parcial: los tokens de la query son subconjunto de los de UN
    #    colaborador ('sanchez' → gabriela sanchez; 'mateo alvarado' exacto ya
    #    cayó arriba). Si matchea a varios, es ambiguo.
    qtokens = set(nq.split())
    matches = [e for e, toks in tokens.items() if qtokens and qtokens <= toks]
    if len(matches) == 1:
        return {"status": "ok", "email": matches[0], "candidates": []}
    if len(matches) > 1:
        return {"status": "ambiguous", "email": None, "candidates": _cands(matches)}

    return {"status": "not_found", "email": None, "candidates": _all()}


def _resolve_collaborator(name_or_email: str) -> str | None:
    """Mapea 'Mateo'/'Gabriela Sánchez'/'gsanchez@...' → email registrado.

    Wrapper de compatibilidad: devuelve el email solo si la resolución es
    inequívoca ('ok'). Para 'ambiguous' o 'not_found' devuelve None (los tool
    handlers usan `_resolve_collaborator_detail` para dar el detalle al user).

    Fase 2 (auditoría A6/C4): SOLO colaboradores registrados en
    KNOWN_COLLABORATORS — nunca un email arbitrario (evita usuario fantasma).
    """
    return _resolve_collaborator_detail(name_or_email).get("email")


def _list_today_activities(user_email: str | None = None) -> dict:
    """Devuelve el state de la semana actual del usuario con info útil para Claude."""
    today_iso = _hoy_ec().isoformat()
    week = activity_state.get_week(user_email)
    out_diarias = []
    out_semanales = []
    for aid, a in week["activities"].items():
        if a["tipo"] == "diaria":
            log = a.get("log", {})
            rec_today = log.get(today_iso)
            out_diarias.append({
                "id": aid,
                "nombre": a["nombre"],
                "meta_diaria": a.get("meta"),
                "unidad": a.get("unidad", ""),
                "valor_hoy": rec_today.get("valor") if rec_today else None,
                "justificacion_hoy": rec_today.get("notas", "") if rec_today else "",
                "marcada_hoy": rec_today is not None,
            })
        else:
            out_semanales.append({
                "id": aid,
                "nombre": a["nombre"],
                "tipo": a["tipo"],
                "avance_actual": a.get("avance", 0),
                "notas": a.get("notas", ""),
                "ultima_actualizacion": a.get("ultima_actualizacion"),
            })
    return {
        "user": user_email or activity_state.DEFAULT_USER,
        "semana": activity_state.week_key(),
        "fecha_hoy": today_iso,
        "diarias": out_diarias,
        "semanales": out_semanales,
    }


def _mark_daily_activity(
    activity_id: str,
    valor: float,
    *,
    user_email: str | None = None,
    fecha: str | None = None,
    justificacion: str = "",
) -> dict:
    rec = activity_state.mark_daily(
        activity_id,
        valor,
        user_email=user_email,
        fecha=fecha,
        notas=justificacion or "",
    )
    return {
        "activity_id": activity_id,
        "valor": rec["valor"],
        "justificacion": rec.get("notas", ""),
        "marcado_at": rec.get("marcado_at"),
    }


def _mark_weekly_progress(
    activity_id: str,
    avance: float,
    *,
    user_email: str | None = None,
    notas: str = "",
) -> dict:
    rec = activity_state.set_weekly_progress(
        activity_id,
        avance,
        user_email=user_email,
        notas=notas or "",
    )
    return {
        "activity_id": activity_id,
        "avance": rec["avance"],
        "notas": rec.get("notas", ""),
        "ultima_actualizacion": rec.get("ultima_actualizacion"),
    }


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
    # Buscar el alias del usuario en COLLABORATORS
    for alias, mapped_email in COLLABORATORS.items():
        if mapped_email == email_lower:
            override = os.environ.get(f"TRACKER_EMAIL_TO_{alias.upper()}")
            if override:
                return [e.strip() for e in override.split(",") if e.strip()]
            break
    # Fallback al global
    global_to = os.environ.get("TRACKER_EMAIL_TO", TRACKER_EMAIL_TO_DEFAULT)
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
CIERRE_CAJA_TO_DEFAULT = (
    "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com"
)
CIERRE_CAJA_CC_DEFAULT = "malvarado@biodegradablesecuador.com"


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

    # Determinar role + título del bloque
    es_asistente = user_email.lower() in ASISTENTE_EMAILS
    if user_email.lower() == "info@biodegradablesecuador.com":
        titulo_bloque = "📦 ASISTENTE 1 — GUAYAQUIL"
        sucursal = "Guayaquil"
    elif user_email.lower() == "quito@biodegradablesecuador.com":
        titulo_bloque = "📦 ASISTENTE 1 — QUITO"
        sucursal = "Quito"
    elif user_email.lower() == "gsanchez@biodegradablesecuador.com":
        titulo_bloque = "👩 GABRIELA SÁNCHEZ"
        sucursal = ""
    elif user_email.lower() == "malvarado@biodegradablesecuador.com":
        titulo_bloque = "👨 MATEO ALVARADO"
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
    diarias_items = [
        (aid, a) for aid, a in week["activities"].items() if a["tipo"] == "diaria"
    ]
    diarias_items = activity_state.sort_activities_by_priority_then_carryover(
        diarias_items, today_iso, yesterday_iso
    )
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
        if aid.startswith("cobranza-"):
            nombre = nombre.replace("📞 Cobranza:", "📞").strip()
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

    # === TikTok seguidores (para users con activity video-tiktok) ===
    tiktok_section = ""
    tiene_tiktok = "video-tiktok" in week.get("activities", {})
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
    tt_video_act = week.get("activities", {}).get("video-tiktok")
    if tt_video_act:
        meta_videos = int(tt_video_act.get("meta_semanal") or 5)
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
        f'{daily_section}{sem_section}{cierre_section}{tiktok_videos_section}{tiktok_section}{choco_section}'
        f'</div>'
        f'</div>'
    )


# Fase 5: _collaborator_block_html (v1) ELIMINADA — dead code (~189 líneas),
# solo se usa _collaborator_block_html_v2 (auditoría C8).

# ============ Consolidated daily summary (Phase O, 2026-06-02) ============
CONSOLIDATED_DAILY_TO_DEFAULT = (
    "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com"
)
CONSOLIDATED_DAILY_CC_DEFAULT = "malvarado@biodegradablesecuador.com"
SUPERVISORS_ONLY_EMAILS: set[str] = {
    "dsanchez@biodegradablesecuador.com",  # debe matchear teams_bot.SUPERVISORS_ONLY
}


ASISTENTE_EMAILS = {
    "info@biodegradablesecuador.com",   # Guayaquil
    "quito@biodegradablesecuador.com",  # Quito
}

# Phase V (2026-06-10): José Solórzano (Asistente 2 GYE — chofer/repartidor)
# tiene un bloque dedicado dentro del consolidado, con su data de logística
# (entregas, salidas, caja chica). NO está en ASISTENTE_EMAILS porque su
# state es diferente (no usa cierre de caja con denominaciones, etc.).
JOSE_EMAIL_CONS = "jsolorzano@biodegradablesecuador.com"
CAJA_CHICA_ALERTA_JOSE = 30.0

# ===== Rotación de asistentes GYE los sábados (2026-06-15) =====
# En Guayaquil hay 2 asistentes que se turnan los sábados:
#   - Asistente 1 GYE: info@  (caja/sucursal)
#   - Asistente 2 GYE: José Solórzano (jsolorzano@, logística/ruta)
# Un sábado trabaja uno, el siguiente el otro. Si un asistente NO llena el
# reporte del sábado, se asume AUSENCIA ESPERADA por el turno rotativo — no
# es error ni reporte pendiente. Solo aplica al recap del sábado (lunes 8 AM).
GYE_ASISTENTE1_EMAIL = "info@biodegradablesecuador.com"
GYE_ROTATIVOS_SABADO = {GYE_ASISTENTE1_EMAIL, JOSE_EMAIL_CONS}


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

    ruta = activity_state.get_ruta_dia(JOSE_EMAIL_CONS, today_iso)
    salidas = ruta.get("salidas", []) or []
    entregas = activity_state.get_entregas_consolidadas_dia(JOSE_EMAIL_CONS, today_iso) or {}
    cc = activity_state.get_caja_chica(JOSE_EMAIL_CONS) or {"inicial": None, "saldo": 0.0, "movimientos": []}
    movs_hoy = activity_state.caja_chica_movimientos_dia(JOSE_EMAIL_CONS, today_iso) or []

    # Rotación GYE de sábados: si José (Asistente 2 GYE) no registró ruta,
    # envíos ni movimientos un sábado, es ausencia esperada por el turno
    # rotativo — no un reporte pendiente.
    if fecha_d.weekday() == 5 and not salidas and not entregas and not movs_hoy:
        return _ausencia_rotativa_block_html(
            "📦 ASISTENTE 2 GYE — José Solórzano", fecha_fmt
        )

    # Resumen de entregas
    n_entregadas = sum(1 for e in entregas.values() if e.get("status") == "entregado")
    n_no_entregadas = sum(1 for e in entregas.values() if e.get("status") == "no_entregado")
    n_pendientes = sum(1 for e in entregas.values() if e.get("status") == "pendiente")
    summary_chips = (
        f'<span style="color:#0d8a3f;font-weight:600">✅ {n_entregadas}</span>  ·  '
        f'<span style="color:#c53030;font-weight:600">❌ {n_no_entregadas}</span>  ·  '
        f'<span style="color:#999">⏳ {n_pendientes}</span>'
    )

    # Salidas
    salidas_visibles = [s for s in salidas if not s.get("marcado_en_oficina")]
    if salidas_visibles:
        salidas_rows = ""
        for i, s in enumerate(salidas_visibles, 1):
            ini = (s.get("inicio_ts") or "")[:16].replace("T", " ")[-8:]
            fin_raw = s.get("fin_ts")
            if fin_raw:
                fin = fin_raw[:16].replace("T", " ")[-8:]
                try:
                    d1 = datetime.fromisoformat(s["inicio_ts"].replace("Z", "+00:00"))
                    d2 = datetime.fromisoformat(fin_raw.replace("Z", "+00:00"))
                    dur = f"{int((d2 - d1).total_seconds() / 60)} min"
                except Exception:
                    dur = "?"
            else:
                fin = "(en curso)"
                dur = "—"
            entr_n = sum(1 for e in (s.get("entregas") or {}).values()
                         if e.get("status") == "entregado")
            salidas_rows += (
                f"<tr><td>#{i}</td><td>{ini}</td><td>{fin}</td>"
                f"<td>{dur}</td><td>{entr_n} entregas</td></tr>"
            )
        salidas_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">🚗 Salidas del día</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:12px;margin-bottom:10px">'
            '<tr style="background:#f0f0f0">'
            '<th style="padding:4px 6px;text-align:left">#</th>'
            '<th style="padding:4px 6px;text-align:left">Inicio</th>'
            '<th style="padding:4px 6px;text-align:left">Fin</th>'
            '<th style="padding:4px 6px;text-align:left">Duración</th>'
            '<th style="padding:4px 6px;text-align:left">Entregas</th>'
            '</tr>' + salidas_rows + '</table>'
        )
    else:
        salidas_html = (
            '<p style="color:#999;font-size:12px;margin:8px 0;">'
            'José no salió a ruta hoy.</p>'
        )

    # Tabla entregas
    if entregas:
        rows = ""
        for fid, e in sorted(entregas.items(), key=lambda kv: kv[1].get("fecha_emision", "")):
            cliente = escape(e.get("cliente", "?"))
            doc = escape(e.get("documento", "?"))
            total = e.get("total", 0)
            status = e.get("status", "pendiente")
            dir_real = e.get("direccion_real") or e.get("direccion_factura") or ""
            obs = e.get("observacion") or ""
            razon = e.get("razon_no_entrega") or ""
            pago = e.get("pago_envio") or 0
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
            obs_html = (
                f'<br><small style="color:#777">{escape(obs)}</small>'
                if obs else ""
            )
            # Phase V: destinos ad-hoc tienen badge especial
            if e.get("adhoc"):
                tipo_a = (e.get("tipo_adhoc") or "entrega").upper()
                doc_extra = f'<br><small style="color:#e67e22;font-weight:600">➕ AD-HOC ({tipo_a})</small>'
            else:
                doc_extra = ""
            total_html = f"${total:,.2f}" if total > 0 else "—"
            rows += (
                "<tr>"
                f"<td>{cliente}<br><small style='color:#777'>{doc}</small>{doc_extra}</td>"
                f"<td style='text-align:right'>{total_html}</td>"
                f"<td>{escape(dir_real)}</td>"
                f"<td style='text-align:right'>{pago_html}</td>"
                f"<td>{badge}{obs_html}</td>"
                "</tr>"
            )
        entregas_html = (
            '<p style="margin:8px 0 4px;font-weight:600;color:#444">📦 Envíos del día</p>'
            '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:10px">'
            '<tr style="background:#f0f0f0">'
            '<th style="padding:6px 8px;text-align:left">Cliente</th>'
            '<th style="padding:6px 8px;text-align:right">Monto</th>'
            '<th style="padding:6px 8px;text-align:left">Dirección final</th>'
            '<th style="padding:6px 8px;text-align:right">Pago terminal</th>'
            '<th style="padding:6px 8px;text-align:left">Estado</th>'
            '</tr>' + rows + '</table>'
        )
    else:
        entregas_html = (
            '<p style="color:#999;font-size:12px;margin:8px 0;">'
            'Sin envíos cargados hoy.</p>'
        )

    # NOTA (2026-06-19): se eliminó la sección dedicada "📝 Observaciones de
    # José" que se renderizaba aparte debajo de la tabla. Las observaciones (y
    # las razones de no entrega) ya se muestran inline en la columna "Estado"
    # de la tabla de envíos — esa es la ÚNICA fuente. No reintroducir una
    # sección de observaciones separada: duplicaba la misma información.

    # Caja chica
    saldo = float(cc.get("saldo") or 0)
    inicial = float(cc.get("inicial") or 0)
    gastos_dia = sum(float(m.get("monto") or 0) for m in movs_hoy if m.get("tipo") == "gasto")
    repos_dia = sum(float(m.get("monto") or 0) for m in movs_hoy if m.get("tipo") == "reposicion")
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
        f'Inicial: <b>${inicial:,.2f}</b>  ·  '
        f'Gastos hoy: <b style="color:#c53030">-${gastos_dia:,.2f}</b>  ·  '
        f'Reposiciones hoy: <b style="color:#0d8a3f">+${repos_dia:,.2f}</b><br>'
        f'<span style="font-size:16px;font-weight:700;color:{saldo_color}">'
        f'Saldo actual: ${saldo:,.2f}</span>{alerta_extra}</p>'
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
        f'📦 ASISTENTE 2 GYE — José Solórzano'
        f'</div>'
        f'<div style="padding:14px 18px;background:{header_bg};">'
        f'<p style="margin:0 0 10px 0;font-size:13px;color:#555;">'
        f'{fecha_fmt}  ·  {summary_chips}</p>'
        f'{salidas_html}'
        f'{entregas_html}'
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

    for aid, a in diarias:
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
    sucursal = "Guayaquil" if user_email == "info@biodegradablesecuador.com" else "Quito"
    icon = "📍"

    # Cobranzas: ids que empiezan con "cobranza-"
    cobranzas_contactadas: list[str] = []
    cobranzas_pendientes: list[str] = []
    for aid, a in week["activities"].items():
        if not aid.startswith("cobranza-"):
            continue
        nombre = a.get("nombre", "")
        # Extraer solo el nombre del cliente (sin emoji ni montos)
        cliente = nombre.replace("📞 Cobranza:", "").strip()
        # Limpiar el monto "— $X (Yd atraso)" para mostrar versión corta
        if " — " in cliente:
            cliente_corto = cliente.split(" — ")[0].strip()
        else:
            cliente_corto = cliente
        rec = (a.get("log") or {}).get(today_iso)
        if rec is None:
            cobranzas_pendientes.append(cliente_corto)
        else:
            valor = rec.get("valor", 0) or 0
            razon = (rec.get("notas") or "").strip()
            if valor > 0:
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

    # Si solo hay uno, mostrar de ancho completo; si dos, en columnas
    info_email = "info@biodegradablesecuador.com"
    quito_email = "quito@biodegradablesecuador.com"
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
        def _orden(d: dict) -> int:
            email = d["email"].lower()
            if email == "gsanchez@biodegradablesecuador.com":
                return 0
            if email == "malvarado@biodegradablesecuador.com":
                return 1
            if email == "info@biodegradablesecuador.com":
                return 2
            # 3 → josé (intercalado abajo)
            if email == "quito@biodegradablesecuador.com":
                return 4
            return 99

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
            # Después de info@ (GYE Asistente 1), insertar bloque de José
            if email_l == "info@biodegradablesecuador.com" and not jose_insertado:
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

    # Destinatarios: Daniel + Gabriela S. + emisor + CC Mateo
    to_default = "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com"
    to_str = os.environ.get("CONFIRMACION_CIERRE_TO", to_default)
    to_list = [e.strip() for e in to_str.split(",") if e.strip()]
    if emisor_email not in to_list:
        to_list.append(emisor_email)
    cc_list = ["malvarado@biodegradablesecuador.com"]

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


# ============ Definición de herramientas (tools para Claude) ============
TOOLS = [
    {
        "name": "get_ventas_dia",
        "description": (
            "Total vendido en un día específico (con IVA, neto de facturas anuladas). "
            "Si no se pasa fecha, asume hoy. Para 'ayer' restar 1 día."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {
                    "type": "string",
                    "description": "Fecha en formato ISO YYYY-MM-DD. Default: hoy.",
                }
            },
        },
    },
    {
        "name": "get_ventas_rango",
        "description": (
            "Total vendido en un rango de fechas (con IVA, sin anuladas). "
            "Útil para MTD, semana, último mes, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {
                    "type": "string",
                    "description": "Fecha inicial ISO YYYY-MM-DD (inclusiva).",
                },
                "fecha_final": {
                    "type": "string",
                    "description": "Fecha final ISO YYYY-MM-DD (inclusiva). Default: hoy.",
                },
            },
            "required": ["fecha_inicial"],
        },
    },
    {
        "name": "get_ventas_por_ciudad",
        "description": (
            "Desglose de ventas por ciudad (Quito UIO vs Guayaquil GYE) en un día o rango. "
            "La ciudad se deduce del prefijo del documento: 001-001=GYE, 001-002=UIO."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {
                    "type": "string",
                    "description": "Fecha inicial ISO YYYY-MM-DD. Default: hoy.",
                },
                "fecha_final": {
                    "type": "string",
                    "description": "Fecha final ISO YYYY-MM-DD. Default: igual a fecha_inicial.",
                },
            },
        },
    },
    {
        "name": "get_top_vendedores",
        "description": (
            "Top N vendedores por monto facturado en el rango. Ordenado descendente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "fecha_final": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "n": {"type": "integer", "description": "Cantidad de vendedores. Default: 5"},
            },
            "required": ["fecha_inicial", "fecha_final"],
        },
    },
    {
        "name": "get_top_clientes",
        "description": (
            "Top N clientes por monto comprado en el rango. Ordenado descendente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "fecha_final": {"type": "string", "description": "ISO YYYY-MM-DD"},
                "n": {"type": "integer", "description": "Cantidad de clientes. Default: 10"},
            },
            "required": ["fecha_inicial", "fecha_final"],
        },
    },
    {
        "name": "get_cumplimiento_mes",
        "description": (
            "KPIs del mes actual: ventas MTD, meta (=mismo mes año anterior × 1.20), "
            "brecha, % cumplimiento, días hábiles pasados/restantes, meta diaria "
            "necesaria para alcanzar el objetivo. Replica la lógica del correo diario."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_saldos_pendientes_clientes",
        "description": (
            "Top N clientes con saldo pendiente (cartera por cliente) basado en "
            "facturas de los últimos 6 meses. Es una aproximación — para cartera "
            "completa con antigüedad por bucket, hay que esperar Phase C."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "Top N clientes. Default: 10"},
            },
        },
    },
    {
        "name": "get_hubspot_leads_ayer",
        "description": "Cuántos leads se crearon ayer en HubSpot y de qué fuente vinieron.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_hubspot_leads_promedio_7d",
        "description": "Promedio diario de leads de los últimos 7 días en HubSpot.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_hubspot_deals_ganados_ayer",
        "description": "Deals cerrados-ganados ayer en HubSpot (count + revenue).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_hubspot_pipeline_abierto",
        "description": "Deals abiertos (no cerrados) en HubSpot (count + valor pipeline).",
        "input_schema": {"type": "object", "properties": {}},
    },
    # === Gestión de equipo (Phase E — solo Data Bot, gerencia) ===
    {
        "name": "list_team_collaborators",
        "description": (
            "Lista los colaboradores del equipo (alias + email) disponibles "
            "para asignarles actividades o recordatorios. Usá esto ANTES de "
            "llamar `add_activity_for_collaborator` o `schedule_reminder_for_collaborator` "
            "para resolver nombres a emails."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_team_workload",
        "description": (
            "Resumen de carga de tareas del equipo: pendientes, en progreso, "
            "vencidas, finalizadas y próximas fechas por colaborador. Usá cuando "
            "el user pregunte '¿cómo va el equipo?', '¿qué tiene pendiente Mateo?', "
            "'mostrame las tareas vencidas'. Si pasás `target_user` muestra solo "
            "ese colaborador; sin él, todo el equipo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Opcional. Nombre o email del colaborador. Vacío = todo el equipo.",
                },
            },
        },
    },
    {
        "name": "create_calendar_meeting_for_collaborator",
        "description": (
            "Crea una reunión/evento en el calendario de Outlook/Teams de un "
            "colaborador (SOLO Daniel o Gabriela Sánchez por ahora). Usá cuando "
            "el user pida 'agéndame reunión con X el martes 10am', 'ponme un "
            "evento el viernes 3pm'. Necesita inicio y fin con hora en ISO."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Nombre o email. Solo Daniel/Gabriela Sánchez habilitados.",
                },
                "subject": {"type": "string", "description": "Título de la reunión."},
                "start": {
                    "type": "string",
                    "description": "Inicio ISO con hora YYYY-MM-DDTHH:MM (hora Ecuador).",
                },
                "end": {
                    "type": "string",
                    "description": "Fin ISO con hora YYYY-MM-DDTHH:MM.",
                },
                "attendees": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Emails de invitados (opcional).",
                },
                "body": {"type": "string", "description": "Descripción (opcional)."},
            },
            "required": ["target_user", "subject", "start", "end"],
        },
    },
    {
        "name": "add_activity_for_collaborator",
        "description": (
            "Asigna una actividad nueva a un colaborador (NO a vos mismo). "
            "Aparece automáticamente en su próximo check-in del Activities Bot. "
            "Usá cuando el user diga 'añade a Mateo a hacer X', 'sumale a Juan "
            "tal cosa esta semana'. Generá un activity_id en kebab-case. "
            "Tipo default: 'unica' (evento único). 'semanal' si es un proyecto "
            "con % avance. 'diaria' si se repite a diario."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Nombre o email del colaborador (ej. 'Mateo' o 'malvarado@...').",
                },
                "activity_id": {
                    "type": "string",
                    "description": "Slug kebab-case (ej. 'bot-whatsapp-customer-service').",
                },
                "nombre": {
                    "type": "string",
                    "description": "Nombre legible (ej. 'Bot WhatsApp para servicio al cliente').",
                },
                "tipo": {
                    "type": "string",
                    "enum": ["diaria", "unica", "semanal"],
                    "description": "Por default 'unica'.",
                },
                "meta": {
                    "type": "number",
                    "description": "Meta opcional (ej. 100 si es %, 1 si 'hacer X').",
                },
                "unidad": {
                    "type": "string",
                    "description": "Unidad de la meta (ej. '%', 'correos').",
                },
                "fecha_limite": {
                    "type": "string",
                    "description": "Fecha límite opcional ISO YYYY-MM-DD. Cuando llegue, "
                                   "el bot le pregunta al colaborador si ya la completó.",
                },
            },
            "required": ["target_user", "activity_id", "nombre"],
        },
    },
    {
        "name": "schedule_reminder_for_collaborator",
        "description": (
            "Programa un recordatorio para que el bot le envíe un mensaje a un "
            "colaborador en una fecha/hora futura. Soporta recurrencia: si el "
            "user dice 'todos los lunes', 'cada 15 del mes', 'todos los días', "
            "etc., pasá el parámetro `recurrence`. Sin recurrence el reminder "
            "es one-shot (se manda una vez y listo). Con recurrence se "
            "reprograma automáticamente después de cada entrega."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Nombre o email del colaborador.",
                },
                "send_at": {
                    "type": "string",
                    "description": "Primera fecha/hora en ISO YYYY-MM-DDTHH:MM (asume EC UTC-5). Ej '2026-06-14T08:00'.",
                },
                "message": {
                    "type": "string",
                    "description": "Mensaje a enviar (sin saludo, el bot agrega '🔔 Recordatorio:').",
                },
                "recurrence": {
                    "type": "string",
                    "enum": [
                        "", "daily", "weekly", "weekdays", "monthly",
                        "weekly_mon", "weekly_tue", "weekly_wed", "weekly_thu",
                        "weekly_fri", "weekly_sat", "weekly_sun",
                    ],
                    "description": (
                        "Recurrencia. '' = one-shot. 'daily' = todos los días. "
                        "'weekly' = mismo día semana en semana. 'weekdays' = "
                        "lun-vie. 'monthly' = mismo día del mes. "
                        "'weekly_<day>' = un día específico (mon, tue, wed, thu, fri, sat, sun)."
                    ),
                },
            },
            "required": ["target_user", "send_at", "message"],
        },
    },
    {
        "name": "forecast_sales_for_month",
        "description": (
            "Proyección baseline de ventas para un mes específico. Devuelve "
            "ventas mismo mes año anterior, growth YoY estimado, y proyección "
            "pesimista/probable/optimista. Usá esto para responder preguntas tipo "
            "'proyectame junio', 'qué esperamos vender en el Q3', etc. "
            "Si el user agrega un FACTOR EXTERNO (cierre canal, guerra, etc.), "
            "PRIMERO llamá esta tool para tener el baseline, DESPUÉS razoná en tu "
            "respuesta cómo el factor afecta el baseline en base a su mix de "
            "productos (que conocés via analyze_product_mix). NO inventes "
            "números fuera de la proyección — usá los rangos como anchor."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "description": "Año target, ej. 2026"},
                "month": {"type": "integer", "description": "Mes target 1-12"},
            },
            "required": ["year", "month"],
        },
    },
    {
        "name": "analyze_product_mix",
        "description": (
            "Analiza el mix de productos en facturación. Devuelve top productos "
            "y, si se pasan keywords, qué % del total corresponde a esos. Útil "
            "para entender exposición a un riesgo específico — ej. 'qué % son "
            "importados de China' (keywords=['china', 'importado', 'asia']), "
            "'qué % son productos PLA' (keywords=['PLA', 'pla']), etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de palabras a buscar en nombres de productos. Vacío = todos.",
                },
                "months_back": {
                    "type": "integer",
                    "description": "Cuántos meses hacia atrás analizar. Default 6.",
                },
            },
        },
    },
    {
        "name": "set_activity_priority_for_collaborator",
        "description": (
            "Marca la PRIORIDAD de una actividad de un colaborador. Solo "
            "gerencia (Daniel/Gabriela) lo usa. Las altas aparecen primero "
            "en su check-in card y, si no se hacen, vuelven a aparecer al "
            "día siguiente subrayadas en rojo (carry-over). Usá esto cuando "
            "el user diga 'marca como prioritaria X de Mateo', 'es importante "
            "que Gabriela haga Y', 'baja la prioridad de Z'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Nombre o email del colaborador.",
                },
                "activity_id": {
                    "type": "string",
                    "description": "ID exacto de la actividad (slug). Si no lo sabés, primero llamá list_team_collaborators para resolver al user, después pedile que confirme el ID exacto o usá uno típico (apollo-correos, cierre-caja-uio, etc.).",
                },
                "priority": {
                    "type": "string",
                    "enum": ["alta", "media", "baja"],
                    "description": "'alta' = urgente (carry-over si no se hace). 'media' = default. 'baja' = puede esperar.",
                },
            },
            "required": ["target_user", "activity_id", "priority"],
        },
    },
    {
        "name": "list_pending_reminders",
        "description": (
            "Lista los recordatorios programados pendientes (no enviados todavía). "
            "Opcionalmente filtrá por colaborador."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_user": {
                    "type": "string",
                    "description": "Opcional: filtrar por un colaborador específico (nombre o email).",
                },
            },
        },
    },
    # === Tracker de actividades (Phase C) ===
    {
        "name": "list_today_activities",
        "description": (
            "Lista las actividades de la semana actual del tracker de Mateo "
            "(diarias y semanales) con sus IDs, metas, valor marcado hoy y avance "
            "acumulado. Usá esto ANTES de marcar para conocer los IDs exactos."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "mark_daily_activity",
        "description": (
            "Marca el resultado de una actividad DIARIA. Por default marca el día "
            "de hoy. Si el usuario dice 'me olvidé del live de ayer', 'el martes "
            "publiqué el video', etc., pasá `fecha` con esa fecha en ISO. "
            "Si la actividad no se hizo, pasá valor=0 Y una justificación. "
            "Si se hizo parcialmente, pasá el valor real + justificación."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "string",
                    "description": "ID exacto de la actividad (ej. 'apollo-correos'). Obtenelo de list_today_activities.",
                },
                "valor": {
                    "type": "number",
                    "description": "Valor numérico hecho. 0 si no se hizo.",
                },
                "fecha": {
                    "type": "string",
                    "description": "Fecha ISO YYYY-MM-DD a marcar (default: hoy). Usá para back-dating: 'ayer', 'el martes', etc.",
                },
                "justificacion": {
                    "type": "string",
                    "description": "Razón si no se hizo o quedó parcial.",
                },
            },
            "required": ["activity_id", "valor"],
        },
    },
    {
        "name": "add_activity_to_week",
        "description": (
            "Agrega una actividad ad-hoc a la semana en curso del usuario. Usá "
            "esto cuando el jefe le sume algo extra: 'añademe reunión con cliente "
            "X', 'me pidieron preparar el reporte de inventario', 'agregame curso "
            "de Excel'. La actividad aparece en el próximo check-in card "
            "automáticamente, junto a las recurrentes. Generá un activity_id "
            "tipo slug (kebab-case) descriptivo y conciso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "string",
                    "description": "Slug único en kebab-case (ej. 'reunion-cliente-x', 'reporte-inventario'). Cortó.",
                },
                "nombre": {
                    "type": "string",
                    "description": "Nombre legible para mostrar en el form/correo (ej. 'Reunión con Cliente X').",
                },
                "tipo": {
                    "type": "string",
                    "enum": ["diaria", "unica", "semanal"],
                    "description": "'unica' por default para tareas que se hacen una vez. 'diaria' si se repite día a día. 'semanal' si es un proyecto con % avance.",
                },
                "meta": {
                    "type": "number",
                    "description": "Meta numérica opcional (ej. 1 si es 'hacer X', 100 si es %).",
                },
                "unidad": {
                    "type": "string",
                    "description": "Unidad de la meta (ej. 'correos', '%', 'horas').",
                },
                "fecha_limite": {
                    "type": "string",
                    "description": "Fecha límite opcional ISO YYYY-MM-DD. Al llegar, el "
                                   "bot te pregunta si la completaste antes de cerrarla.",
                },
            },
            "required": ["activity_id", "nombre"],
        },
    },
    {
        "name": "remove_activity_from_week",
        "description": (
            "Quita una actividad ad-hoc o recurrente de la semana en curso. Usá "
            "esto solo si el usuario explícitamente pide quitar algo. NO la "
            "borra del template para próximas semanas — solo de la semana actual."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "string",
                    "description": "ID exacto de la actividad a quitar.",
                },
            },
            "required": ["activity_id"],
        },
    },
    {
        "name": "mark_weekly_progress",
        "description": (
            "Actualiza el % de avance de una actividad SEMANAL (proyecto). Usá "
            "esto cuando el usuario reporta avance en proyectos como 'códigos "
            "Contifico avancé 10%', 'chatbot logística 70%'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity_id": {
                    "type": "string",
                    "description": "ID exacto (ej. 'codigos-contifico'). Sacalo de list_today_activities.",
                },
                "avance": {
                    "type": "number",
                    "description": "Avance acumulado total en %, de 0 a 100. NO incremento, sino el valor absoluto nuevo.",
                },
                "notas": {
                    "type": "string",
                    "description": "Notas breves de qué se avanzó (opcional).",
                },
            },
            "required": ["activity_id", "avance"],
        },
    },
    {
        "name": "send_daily_summary_email",
        "description": (
            "Genera el resumen de actividades de HOY y lo envía por correo a "
            "Daniel y Gabriela. Llamá esto SIEMPRE que el usuario haya respondido "
            "al check-in del día (decir qué hizo). Si el usuario solo marca una "
            "actividad casual sin contexto de cierre del día, NO llames esto — "
            "solo marcá y confirmá. Es seguro llamarlo dos veces el mismo día "
            "(actualiza el resumen)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _system_prompt(mode: str = "data", user_email: str | None = None) -> str:
    """Devuelve el system prompt según el modo del bot.

    mode='data': para Data Bot (gerencia). Tools de Contifico + HubSpot.
    mode='activities': para Activities Bot (colaboradores). Tools de tracker.
    """
    if mode == "activities":
        return _system_prompt_activities(user_email)
    if mode == "data":
        return _system_prompt_data()
    # Fase 4: el modo 'full' legacy llamaba _system_prompt_full(), función
    # que NO EXISTE — cualquier mode desconocido crasheaba con NameError
    # (bug latente detectado por ruff F821). Mejor explícito:
    raise ValueError(f"mode desconocido: {mode!r} (válidos: 'data', 'activities')")


def _system_prompt_activities(user_email: str | None = None) -> str:
    now_ec = datetime.now(LOCAL_TZ)
    target_email = (user_email or activity_state.DEFAULT_USER).lower()
    target_humano = _humano(target_email)
    es_supervisor = target_email in SUPERVISORS_ONLY_EMAILS
    es_no_identificado = target_email.startswith("unidentified-")

    # Cuenta sin vincular: el AAD del usuario no está registrado, así que el bot
    # no sabe quién es ni qué permisos tiene (un supervisor como Daniel aparece
    # acá si su AAD nunca se mapeó → pierde las tools de delegación). Damos un
    # mensaje claro y accionable en vez de improvisar o "perder" la petición.
    no_id_block = ""
    if es_no_identificado:
        no_id_block = """

⚠️ CUENTA NO VINCULADA:
El AAD de este usuario no está registrado, así que NO sé con certeza quién es
ni qué puede hacer. NO asumas que es un colaborador ni un supervisor.
Si pide asignar/delegar algo a otra persona, o marcar actividades, respondé:
"Tu cuenta todavía no está vinculada al sistema, por eso no puedo registrar ni
delegar nada de forma segura. Escribile a Mateo (malvarado@biodegradablesecuador.com)
para que vincule tu usuario — tu AAD id aparece en los logs del bot." NO inventes
ni crees actividades a ciegas.
"""

    # Phase V (2026-06-11): si es supervisor (Daniel), bloque extra que le dice
    # cómo asignar actividades a OTROS colaboradores.
    supervisor_block = ""
    if es_supervisor:
        supervisor_block = f"""

⚠️ USUARIO ESPECIAL — SUPERVISOR:
{target_humano} es SUPERVISOR. NO trackea actividades propias.
Su rol es ASIGNAR actividades a otros colaboradores y verificar que las hagan.

CUANDO {target_humano} TE PIDA AGREGAR ACTIVIDADES:
- SIEMPRE asumí que son PARA OTRO colaborador, NO para él mismo.
- Si menciona un nombre (ej. "agregale a Gabriela X cosa", "que Mateo haga Y"),
  usá `add_activity_for_collaborator` con target_user=email del colaborador.
- Si NO menciona nombre, preguntá: "¿Para quién?" — NUNCA las pongas en su
  propio tracker.
- Para resolver nombre → email, usá `list_team_collaborators` primero si dudás.

OTRAS ACCIONES DE SUPERVISOR DISPONIBLES:
- `schedule_reminder_for_collaborator`: programa recordatorios a un colab.
- `set_activity_priority_for_collaborator`: marca prioridad de una activity ya creada.
- `list_pending_reminders`: lista reminders programados.

NO uses `add_activity_to_week` ni `mark_daily_activity` con el email de
{target_humano} — él NO tiene actividades propias.
"""

    return f"""Eres el asistente de tracking de actividades de Biodegradables Ecuador.

Estás hablando con: {target_humano}
Hoy es {now_ec.strftime("%A %d de %B de %Y, %H:%M")} (Ecuador, UTC-5).
{no_id_block}{supervisor_block}
NO TENÉS acceso a datos de ventas, HubSpot, ni nada de gerencia. Si el usuario
pregunta "cuánto vendimos hoy", "cómo va el mes", etc., decile amablemente:
"Para consultas de ventas, cartera, leads y deals, usá el **Data Bot** (otro
bot en Teams). Yo solo manejo el tracker de actividades del equipo."

CÓMO MARCAR ACTIVIDADES (flujo casual o cierre del día):
1. PRIMERO llamá `list_today_activities` para conocer los IDs y estado actual
2. Por cada actividad mencionada, llamá `mark_daily_activity` (diarias) o
   `mark_weekly_progress` (semanales)
3. Si el usuario está cerrando el día (marcó varias actividades, dijo "ya
   está", "mandalo", "listo"), llamá `send_daily_summary_email` para
   enviar resumen a supervisores
4. Si marcó solo UNA actividad casualmente sin cerrar día, NO mandes email —
   solo confirmá la marca
5. Respondele al usuario con un resumen visual breve y confirmación

BACK-DATING (marcar algo de un día pasado):
Si el usuario dice "me olvidé del live de ayer, sí lo subí" o "el martes hice
80 correos", pasá `fecha` en formato ISO al `mark_daily_activity`. Ejemplos:
- "ayer" → fecha=día anterior a hoy
- "el martes" → fecha del último martes pasado
- "el lunes 26" → fecha exacta
NO mandes email en estos casos (es un retroactivo, no cierre del día).

AGREGAR ACTIVIDADES AD-HOC (algo que sumó el jefe o algo extra):
Si el usuario dice "añademe reunión con cliente X", "me pidieron preparar el
reporte trimestral", "agregame el curso de Excel para esta semana", usá
`add_activity_to_week`. Generá un `activity_id` slug descriptivo
(kebab-case) y un nombre legible.
- Default tipo="unica" si es un evento único
- tipo="diaria" si se repite cada día
- tipo="semanal" si es un proyecto con % avance acumulado
La actividad aparece en el próximo check-in automáticamente.

QUITAR ACTIVIDADES:
Solo si el usuario lo pide explícito ("quitame X de la semana"), usá
`remove_activity_from_week`.

REGLAS GENERALES:
- Marcá SOLO lo que el usuario dijo explícitamente. No asumas.
- Si "parcial" (debajo de meta), incluí `justificacion`.
- Si no hizo algo, marcá valor=0 con la razón.
- IDs exactos vienen de `list_today_activities` para marcar, NO los inventes.
- Para `add_activity_to_week` sí podés crear un ID nuevo en kebab-case."""


def _system_prompt_data() -> str:
    now_ec = datetime.now(LOCAL_TZ)
    base_prompt = f"""Eres el asistente de datos comerciales de Biodegradables Ecuador.

Respondes preguntas sobre la operación comercial usando 2 fuentes:
- **Contifico** (ERP, source of truth en tiempo real para ventas, clientes, vendedores)
- **HubSpot** (CRM para leads, deals, pipeline)

Tu trabajo: interpretar la pregunta, llamar las herramientas correctas, y devolver
una respuesta clara y breve en español.

CONTEXTO:
- Hoy es {now_ec.strftime("%A %d de %B de %Y, %H:%M")} (Ecuador, UTC-5)
- Empresa: Biodegradables Ecuador — distribución de productos biodegradables
- 2 sucursales: Quito (UIO) y Guayaquil (GYE)
- Datos de Contifico son **en vivo** (no cache de 4 refrescos/día como PBI)

CÓMO USAR LAS HERRAMIENTAS:
- "cuánto vendimos hoy/ayer" → `get_ventas_dia` con fecha
- "ventas de la semana/mes" → `get_ventas_rango`
- "Quito vs Guayaquil" → `get_ventas_por_ciudad`
- "quién vendió más" → `get_top_vendedores`
- "top clientes / mejores clientes del mes" → `get_top_clientes`
- "cómo va el mes / cumplimiento de meta" → `get_cumplimiento_mes`
- "clientes con deuda / cartera por cobrar / top deudores" → `get_saldos_pendientes_clientes`
- "leads / deals / pipeline" → tools de HubSpot

PROYECCIONES (Phase H — escenarios para gerencia):
Podés ayudar a gerencia con proyecciones de ventas y análisis de escenarios.
Tools disponibles:
- `forecast_sales_for_month(year, month)` — baseline matemático (same-month-LY × YoY growth)
- `analyze_product_mix(keywords, months_back)` — qué % de mix son productos X

PATRÓN para preguntas tipo "proyectame junio si pasa X":
1. Llamá `forecast_sales_for_month(año, mes)` para tener el rango baseline
2. Si el escenario menciona un riesgo específico (ej. "canal Panamá cerrado",
   "guerra Irán", "tarifas a China"), llamá `analyze_product_mix(keywords)`
   con keywords relevantes para entender la exposición real del negocio
3. Razoná el ajuste sobre el baseline en TU respuesta:
   - Cuánto del mix está expuesto al factor
   - Magnitud típica del impacto (delays, costos, supply)
   - Rango ajustado pesimista/probable/optimista
4. Cerrá con 2-4 acciones concretas recomendadas
5. SIEMPRE aclará: "Esto es un escenario razonado, no una predicción exacta.
   Útil para planificación de contingencia."

REGLAS al proyectar:
- NUNCA inventes números fuera del rango del baseline sin justificación clara
- Si NO tenés data histórica suficiente, decile al user honestamente
- Si el factor externo no afecta el mix del negocio (ej. "guerra Yemen" pero
  Biodegradables no importa de Yemen), explicalo y no infles ajustes
- Para factores muy especulativos ("y si baja el precio del litio"), aclarar
  que es razonamiento sin data específica

Ejemplos:
- "proyecta junio" → forecast_sales_for_month(2026, 6) + respuesta directa
- "proyecta junio si cierran Panamá" → forecast + analyze_product_mix(['china','asia']) + razonamiento
- "qué % de mi mix es PLA" → analyze_product_mix(['PLA','pla','poliláctico'])

GESTIÓN DE EQUIPO (Phase E — solo gerencia):
Como Daniel y Gabriela son supervisores, pueden asignar tareas y recordatorios
a otros colaboradores. Patrones típicos:

- "Añade a Mateo a esta semana hacer un bot de WhatsApp"
  → llamá `list_team_collaborators` (para resolver "Mateo" → email)
  → llamá `add_activity_for_collaborator` con target_user, activity_id (slug
     que vos generás), nombre legible, tipo='unica' o 'semanal'
  → confirmá: "Listo, le agregué a Mateo: 'Bot WhatsApp...'. Le aparecerá
     en su próximo check-in (lun 4:30 PM)."

- "Recordale a Mateo un día antes que el 15 entrega el reporte mensual"
  → resolvé Mateo → email
  → calculá la fecha: si entrega el 15, el recordatorio va el 14 a una hora
     razonable (default 8 AM Ecuador)
  → llamá `schedule_reminder_for_collaborator` con send_at en ISO
  → confirmá: "Listo, le programé recordatorio para el 14 a las 8 AM:
     'Mañana entregás el reporte mensual de ventas'"

- "Qué recordatorios le mandé a Mateo?"
  → llamá `list_pending_reminders` con filtro target_user='Mateo'
  → mostrá fecha + mensaje

REGLAS gestión:
- Generá activity_id en kebab-case descriptivo (ej. 'bot-whatsapp-cliente',
  'reporte-mensual-ventas-junio'). Cortó, sin tildes, sin espacios.
- Para recordatorios, INTERPRETÁ la fecha del lenguaje natural y devolvé
  ISO YYYY-MM-DDTHH:MM. "Mañana 8 AM" = mañana 08:00. "El martes" = próximo
  martes 08:00. "Un día antes del 15 de junio" = 14 de junio 08:00.
- NO inventés colaboradores. Si el nombre no matchea, mostrá la lista con
  list_team_collaborators y pediles al usuario que aclare.
- NO podés marcar actividades por otros ni ver su progreso detallado —
  eso es responsabilidad de cada colaborador desde su Activities Bot.

NO TENÉS acceso al tracker de actividades. Si el usuario pregunta "marca
apollo", "cómo van mis actividades", etc., decile: "El tracker de actividades
es otro bot en Teams (Activities Bot)."

FECHAS:
- Usa formato ISO YYYY-MM-DD al llamar las tools.
- "hoy" = {now_ec.date().isoformat()}, "ayer" = {(now_ec.date() - timedelta(days=1)).isoformat()}
- "este mes" = del 01 al {now_ec.date().isoformat()}
- Si el usuario pregunta "este año" usa enero 1 al día de hoy.

REGLAS DE RESPUESTA:
- Sé directo y breve. 1-3 párrafos máximo.
- Formato monetario: $12,345 (coma de miles, sin decimales para amounts >$1,000).
- Si una herramienta devuelve 0 ventas, no inventes datos — di "no hay datos para ese período".
- Si la pregunta requiere una ACCIÓN (enviar correo, modificar, crear), explicá que esa
  función aún no está implementada y sugerí la alternativa.
- Cuando suma valor, compará proactivamente con período anterior (ej. ayer vs anteayer,
  este mes vs el anterior).
- Si te preguntan por inventario, productos individuales, refresh times, o queries DAX,
  **avisá que esas funciones aún no están en esta versión del bot** — están en el roadmap.

WEB SEARCH (Phase I — para contexto actual):
Tenés acceso a la herramienta `web_search`. Usala cuando:
- El user pregunta sobre PROYECCIONES + un FACTOR EXTERNO específico
  (ej. "qué pasa con junio si hay paro nacional"). Buscá el estado actual.
- El user pregunta sobre noticias o contexto actual (ej. "cómo está el
  dólar paralelo hoy", "qué pasó con el Canal de Panamá").
- Necesitás verificar un dato del CONTEXTO ACTUAL antes de afirmarlo.

NO uses web_search cuando la pregunta es solo sobre datos internos
(Contifico, HubSpot, ventas, leads) — ahorrá tiempo y costo.

Citá las fuentes con fecha cuando uses web_search ("según El Comercio del 28/05/2026")."""
    # Phase I: inyectar el news brief del día si está disponible
    brief_section = news_brief.format_brief_for_prompt()
    return base_prompt + brief_section


# ============ Despacho de tools ============
# Sets para filtrado de tools por modo del bot
TRACKER_TOOL_NAMES = {
    "list_today_activities",
    "mark_daily_activity",
    "mark_weekly_progress",
    "send_daily_summary_email",
    "add_activity_to_week",
    "remove_activity_from_week",
}

# Phase V (2026-06-11): Tools EXTRA para supervisores en el Activities Bot.
# Daniel puede asignar actividades a cualquier colaborador desde su chat.
SUPERVISOR_EXTRA_TOOLS_ACTIVITIES = {
    "list_team_collaborators",
    "list_team_workload",
    "add_activity_for_collaborator",
    "schedule_reminder_for_collaborator",
    "set_activity_priority_for_collaborator",
    "list_pending_reminders",
    "create_calendar_meeting_for_collaborator",
}

# Tools de AGENDA (reuniones + recordatorios al calendario) — disponibles para la
# gerencia con calendario habilitado = CALENDAR_SYNC_USERS (Daniel + Gabriela
# Sánchez). Gabriela NO está en SUPERVISORS_ONLY (sí trackea actividades propias),
# por eso se le habilita este subset aparte (2026-06-18).
SCHEDULING_TOOLS = {
    "list_team_collaborators",
    "create_calendar_meeting_for_collaborator",
    "schedule_reminder_for_collaborator",
    "list_pending_reminders",
}

# Phase V (2026-06-11): mapa email → nombre humano. Evita que Claude se
# invente nombres cuando solo recibe el email crudo en el system prompt.
EMAIL_TO_NAME = {
    "dsanchez@biodegradablesecuador.com": "Daniel Sánchez",
    "gsanchez@biodegradablesecuador.com": "Gabriela Sánchez",
    "malvarado@biodegradablesecuador.com": "Mateo Alvarado",
    "info@biodegradablesecuador.com": "Gabriela Bravo (Asistente 1 GYE)",
    "quito@biodegradablesecuador.com": "Gladys López (Asistente 1 UIO)",
    "jsolorzano@biodegradablesecuador.com": "José Solórzano (Asistente 2 GYE)",
}


def _humano(email: str | None) -> str:
    """Devuelve 'Nombre Apellido (email)' para el system prompt."""
    if not email:
        return "Usuario desconocido"
    name = EMAIL_TO_NAME.get(email.lower())
    if name:
        return f"{name} ({email})"
    return email
DATA_TOOL_NAMES = {
    "get_ventas_dia", "get_ventas_rango", "get_ventas_por_ciudad",
    "get_top_vendedores", "get_top_clientes", "get_cumplimiento_mes",
    "get_saldos_pendientes_clientes",
    "get_hubspot_leads_ayer", "get_hubspot_leads_promedio_7d",
    "get_hubspot_deals_ganados_ayer", "get_hubspot_pipeline_abierto",
    "list_team_collaborators",
    "list_team_workload",
    "add_activity_for_collaborator",
    "schedule_reminder_for_collaborator",
    "set_activity_priority_for_collaborator",
    "list_pending_reminders",
    "create_calendar_meeting_for_collaborator",
    "forecast_sales_for_month",
    "analyze_product_mix",
}


def _missing_args(args: dict, required: tuple[str, ...]) -> list[str]:
    """Devuelve los nombres de args requeridos que faltan o vienen vacíos.

    Robustece contra el modelo omitiendo un campo 'required' del schema: en vez
    de un KeyError críptico, el tool devuelve un error legible y accionable.
    """
    out: list[str] = []
    for k in required:
        v = args.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(k)
    return out


def _require_collaborator(
    tool_name: str, args: dict, *, requested_by: str | None = None
) -> dict:
    """Resuelve `args['target_user']` a un colaborador registrado.

    Centraliza la validación de TODOS los tools de delegación: un único lugar
    para el manejo de 'falta target', 'no encontrado' y 'ambiguo'. Devuelve
    `{'target': email}` si resolvió, o `{'error': msg}` listo para serializar.
    Loguea cada intento para facilitar futuras investigaciones.
    """
    raw = args.get("target_user")
    if raw is None or not str(raw).strip():
        logger.warning("%s: sin target_user (requested_by=%s)",
                       tool_name, requested_by or "?")
        return {"error": "No me dijiste a QUIÉN asignar. Decime el nombre del "
                         "colaborador (ej. 'Gabriela Sánchez', 'Mateo')."}

    detail = _resolve_collaborator_detail(raw)
    status = detail["status"]
    if status == "ok":
        return {"target": detail["email"]}

    if status == "ambiguous":
        opciones = ", ".join(
            f"{c['nombre']} ({c['email']})" for c in detail["candidates"]
        )
        logger.warning("%s: '%s' ambiguo → %s (requested_by=%s)",
                       tool_name, raw, opciones, requested_by or "?")
        return {"error": f"'{raw}' coincide con varios colaboradores: "
                         f"{opciones}. ¿A cuál te referís?"}

    # not_found
    disponibles = ", ".join(
        f"{c['nombre']} ({c['email']})" for c in detail["candidates"]
    )
    logger.warning("%s: colaborador no encontrado '%s' (requested_by=%s)",
                   tool_name, raw, requested_by or "?")
    return {"error": f"No encontré a '{raw}' entre los colaboradores "
                     f"registrados. Disponibles: {disponibles or '(ninguno)'}. "
                     f"Si falta alguien, hay que registrarlo en "
                     f"KNOWN_COLLABORATORS."}


def _call_tool(name: str, args: dict, *, user_email: str | None = None) -> str:
    """Ejecuta una herramienta y devuelve el resultado como string JSON."""
    try:
        if name == "get_ventas_dia":
            fecha = _parse_date(args.get("fecha"))
            return json.dumps(
                contifico_client.ventas_dia(fecha),
                ensure_ascii=False,
            )

        if name == "get_ventas_rango":
            fi = _parse_date(args.get("fecha_inicial"))
            ff = _parse_date(args.get("fecha_final"), default=_hoy_ec())
            return json.dumps(
                contifico_client.ventas_rango(fi, ff),
                ensure_ascii=False,
            )

        if name == "get_ventas_por_ciudad":
            fi = _parse_date(args.get("fecha_inicial"))
            ff = _parse_date(args.get("fecha_final"), default=fi)
            return json.dumps(
                contifico_client.ventas_por_ciudad(fi, ff),
                ensure_ascii=False,
            )

        if name == "get_top_vendedores":
            fi = _parse_date(args.get("fecha_inicial"))
            ff = _parse_date(args.get("fecha_final"))
            n = int(args.get("n") or 5)
            return json.dumps(
                contifico_client.top_vendedores(fi, ff, n),
                ensure_ascii=False,
            )

        if name == "get_top_clientes":
            fi = _parse_date(args.get("fecha_inicial"))
            ff = _parse_date(args.get("fecha_final"))
            n = int(args.get("n") or 10)
            return json.dumps(
                contifico_client.top_clientes(fi, ff, n),
                ensure_ascii=False,
            )

        if name == "get_cumplimiento_mes":
            return json.dumps(
                contifico_client.cumplimiento_mes(),
                ensure_ascii=False,
            )

        if name == "get_saldos_pendientes_clientes":
            n = int(args.get("n") or 10)
            return json.dumps(
                contifico_client.saldos_pendientes_clientes(n=n),
                ensure_ascii=False,
            )

        if name == "get_hubspot_leads_ayer":
            return json.dumps(hubspot_client.leads_ayer(), ensure_ascii=False)

        if name == "get_hubspot_leads_promedio_7d":
            return json.dumps(
                {"promedio_dia": hubspot_client.leads_promedio_7d()},
                ensure_ascii=False,
            )

        if name == "get_hubspot_deals_ganados_ayer":
            return json.dumps(hubspot_client.deals_ganados_ayer(), ensure_ascii=False)

        if name == "get_hubspot_pipeline_abierto":
            return json.dumps(hubspot_client.pipeline_abierto(), ensure_ascii=False)

        # === Gestión de equipo (Phase E — Data Bot) ===
        if name == "list_team_collaborators":
            seen: dict[str, dict] = {}
            for alias, email in COLLABORATORS.items():
                email = email.strip().lower()
                entry = seen.setdefault(
                    email,
                    {"email": email, "nombre": EMAIL_TO_NAME.get(email, email),
                     "alias": []},
                )
                entry["alias"].append(alias)
            return json.dumps(list(seen.values()), ensure_ascii=False)

        if name == "list_team_workload":
            filt = args.get("target_user")
            target = None
            if filt:
                detail = _resolve_collaborator_detail(filt)
                if detail["status"] != "ok":
                    return json.dumps({
                        "error": f"No pude resolver '{filt}' a un colaborador. "
                                 f"Usá list_team_collaborators para ver los nombres.",
                    }, ensure_ascii=False)
                target = detail["email"]
            if target:
                return json.dumps(_workload_rollup(target), ensure_ascii=False)
            rollups = [_workload_rollup(e) for e in _team_collaborator_emails()]
            return json.dumps({"equipo": rollups}, ensure_ascii=False)

        if name == "create_calendar_meeting_for_collaborator":
            res = _require_collaborator(name, args, requested_by=user_email)
            if "error" in res:
                return json.dumps(res, ensure_ascii=False)
            target = res["target"]
            if target.lower() not in {e.lower() for e in core_config.CALENDAR_SYNC_USERS}:
                return json.dumps({
                    "error": f"El calendario solo está habilitado para Daniel y "
                             f"Gabriela Sánchez por ahora ({_humano(target)} no).",
                }, ensure_ascii=False)
            miss = _missing_args(args, ("subject", "start", "end"))
            if miss:
                return json.dumps({
                    "error": f"Faltan datos para la reunión: {', '.join(miss)}. "
                             f"Necesito título, inicio y fin con hora (ISO).",
                }, ensure_ascii=False)
            try:
                ev = graph_calendar_app.create_meeting(
                    target,
                    subject=args["subject"],
                    start_iso=args["start"],
                    end_iso=args["end"],
                    body_html=args.get("body", ""),
                    attendees=args.get("attendees"),
                )
            except Exception as e:
                logger.warning("create_calendar_meeting_for_collaborator falló "
                               "(target=%s): %s", target, e)
                return json.dumps({
                    "error": f"No pude crear la reunión: {e}",
                }, ensure_ascii=False)
            logger.info("Reunión '%s' creada en calendario de %s por %s",
                        args["subject"], target, user_email or "?")
            return json.dumps({
                "ok": True,
                "target": target,
                "target_nombre": _humano(target),
                "subject": args["subject"],
                "event_id": ev.get("id"),
                "web_link": ev.get("webLink"),
            }, ensure_ascii=False)

        if name == "add_activity_for_collaborator":
            res = _require_collaborator(name, args, requested_by=user_email)
            if "error" in res:
                return json.dumps(res, ensure_ascii=False)
            target = res["target"]
            miss = _missing_args(args, ("activity_id", "nombre"))
            if miss:
                logger.warning("add_activity_for_collaborator: faltan args %s "
                               "(target=%s)", miss, target)
                return json.dumps({
                    "error": f"Faltan datos para crear la actividad: {', '.join(miss)}. "
                             f"Pedile al usuario que aclare qué actividad asignar.",
                }, ensure_ascii=False)
            tipo = args.get("tipo") or "unica"
            try:
                rec = activity_state.add_adhoc(
                    args["activity_id"],
                    args["nombre"],
                    user_email=target,
                    tipo=tipo,
                    meta=args.get("meta"),
                    unidad=args.get("unidad", ""),
                    fecha_limite=args.get("fecha_limite"),
                )
            except ValueError as e:
                # Actividad ya asignada (id duplicado) o tipo inválido.
                logger.warning("add_activity_for_collaborator rechazada (target=%s, "
                               "id=%s): %s", target, args.get("activity_id"), e)
                msg = str(e)
                if "Ya existe" in msg:
                    msg = (f"'{args['activity_id']}' ya está asignada a "
                           f"{_humano(target)} esta semana. Si querés cambiarla, "
                           f"usá otro id o quitá la anterior primero.")
                return json.dumps({"error": msg}, ensure_ascii=False)
            logger.info("Actividad '%s' asignada a %s por %s",
                        args["activity_id"], target, user_email or "?")
            return json.dumps({
                "ok": True,
                "target": target,
                "target_nombre": _humano(target),
                "activity_id": args["activity_id"],
                "nombre": rec["nombre"],
                "tipo": rec["tipo"],
            }, ensure_ascii=False)

        if name == "schedule_reminder_for_collaborator":
            res = _require_collaborator(name, args, requested_by=user_email)
            if "error" in res:
                return json.dumps(res, ensure_ascii=False)
            target = res["target"]
            miss = _missing_args(args, ("send_at", "message"))
            if miss:
                logger.warning("schedule_reminder_for_collaborator: faltan args %s "
                               "(target=%s)", miss, target)
                return json.dumps({
                    "error": f"Faltan datos para programar el recordatorio: "
                             f"{', '.join(miss)}. Necesito fecha/hora y el mensaje.",
                }, ensure_ascii=False)
            try:
                rec = reminders.add_reminder(
                    target,
                    args["send_at"],
                    args["message"],
                    created_by=user_email or "",
                    recurrence=args.get("recurrence", "") or "",
                )
            except (ValueError, KeyError) as e:
                logger.warning("schedule_reminder_for_collaborator rechazado "
                               "(target=%s, send_at=%s): %s",
                               target, args.get("send_at"), e)
                return json.dumps({
                    "error": f"No pude programar el recordatorio: {e}. "
                             f"Revisá que la fecha/hora esté en formato "
                             f"ISO (YYYY-MM-DDTHH:MM).",
                }, ensure_ascii=False)
            logger.info("Reminder %s para %s @ %s creado por %s",
                        rec["id"], target, rec["send_at"], user_email or "?")
            # Además del recordatorio por CHAT, si el target tiene calendario
            # habilitado (Daniel/Gabriela), crear también un evento con alerta en
            # su calendario de Outlook/Teams (2026-06-18).
            calendario = None
            if target.lower() in {e.lower() for e in core_config.CALENDAR_SYNC_USERS}:
                try:
                    cuando = (args["send_at"] or "")[:19]  # 'YYYY-MM-DDTHH:MM:SS' sin offset
                    ev = graph_calendar_app.create_reminder_event(
                        target,
                        subject=f"⏰ Recordatorio: {args['message'][:80]}",
                        when_iso=cuando,
                        body_html=args["message"],
                    )
                    calendario = ev.get("webLink") or "creado"
                    logger.info("Reminder %s también agendado en calendario de %s",
                                rec["id"], target)
                except Exception as e:  # noqa: BLE001
                    logger.warning("No se pudo crear evento de calendario para "
                                   "reminder %s (target=%s): %s", rec["id"], target, e)
            return json.dumps({
                "ok": True,
                "reminder_id": rec["id"],
                "target": target,
                "target_nombre": _humano(target),
                "send_at": rec["send_at"],
                "message": rec["message"],
                "recurrence": rec.get("recurrence", ""),
                "calendario": calendario,
            }, ensure_ascii=False)

        if name == "list_pending_reminders":
            filt = args.get("target_user")
            target = None
            if filt:
                detail = _resolve_collaborator_detail(filt)
                if detail["status"] != "ok":
                    # Filtro no resoluble: no rompemos, listamos todos.
                    logger.info("list_pending_reminders: filtro '%s' no resuelto "
                                "(%s) — listo todos", filt, detail["status"])
                else:
                    target = detail["email"]
            pending = reminders.list_reminders(
                target_user=target, only_pending=True
            )
            return json.dumps(pending, ensure_ascii=False)

        if name == "set_activity_priority_for_collaborator":
            res = _require_collaborator(name, args, requested_by=user_email)
            if "error" in res:
                return json.dumps(res, ensure_ascii=False)
            target = res["target"]
            miss = _missing_args(args, ("activity_id", "priority"))
            if miss:
                return json.dumps({
                    "error": f"Faltan datos: {', '.join(miss)}.",
                }, ensure_ascii=False)
            try:
                rec = activity_state.set_priority(
                    args["activity_id"],
                    args["priority"],
                    user_email=target,
                )
                return json.dumps({
                    "ok": True,
                    "target": target,
                    "target_nombre": _humano(target),
                    "activity_id": args["activity_id"],
                    "priority": rec.get("priority"),
                    "nombre": rec.get("nombre"),
                }, ensure_ascii=False)
            except ValueError as e:
                logger.warning("set_activity_priority_for_collaborator (target=%s, "
                               "id=%s): %s", target, args.get("activity_id"), e)
                return json.dumps({"error": str(e)}, ensure_ascii=False)

        # === Proyecciones (Phase H — Data Bot) ===
        if name == "forecast_sales_for_month":
            return json.dumps(
                forecasting.forecast_baseline(
                    int(args["year"]), int(args["month"])
                ),
                ensure_ascii=False,
            )

        if name == "analyze_product_mix":
            return json.dumps(
                forecasting.product_mix_breakdown(
                    keywords=args.get("keywords"),
                    months_back=int(args.get("months_back") or 6),
                ),
                ensure_ascii=False,
            )

        # === Tracker de actividades (Phase C/D — per-user) ===
        if name == "list_today_activities":
            return json.dumps(
                _list_today_activities(user_email=user_email),
                ensure_ascii=False,
            )

        if name == "mark_daily_activity":
            return json.dumps(
                _mark_daily_activity(
                    args["activity_id"],
                    float(args["valor"]),
                    user_email=user_email,
                    fecha=args.get("fecha"),
                    justificacion=args.get("justificacion", ""),
                ),
                ensure_ascii=False,
            )

        if name == "add_activity_to_week":
            rec = activity_state.add_adhoc(
                args["activity_id"],
                args["nombre"],
                user_email=user_email,
                tipo=args.get("tipo", "unica"),
                meta=args.get("meta"),
                unidad=args.get("unidad", ""),
                fecha_limite=args.get("fecha_limite"),
            )
            return json.dumps(
                {"ok": True, "activity_id": args["activity_id"],
                 "nombre": rec["nombre"], "tipo": rec["tipo"]},
                ensure_ascii=False,
            )

        if name == "remove_activity_from_week":
            ok = activity_state.remove_activity(
                args["activity_id"], user_email=user_email
            )
            return json.dumps(
                {"ok": ok, "activity_id": args["activity_id"]},
                ensure_ascii=False,
            )

        if name == "mark_weekly_progress":
            return json.dumps(
                _mark_weekly_progress(
                    args["activity_id"],
                    float(args["avance"]),
                    user_email=user_email,
                    notas=args.get("notas", ""),
                ),
                ensure_ascii=False,
            )

        if name == "send_daily_summary_email":
            return json.dumps(
                _send_daily_summary_email(user_email=user_email),
                ensure_ascii=False,
            )

        return json.dumps({"error": f"tool {name} no implementada"})
    except Exception as e:
        return json.dumps({"error": str(e), "tool": name})


WEB_SEARCH_TOOL_DEF = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": 2,  # Bajado de 5 a 2: cada search trae mucho contenido y empuja tokens
}


def _tools_for_mode(mode: str, user_email: str | None = None) -> list:
    """Filtra TOOLS según el modo del bot. Data mode incluye web_search.

    Phase V (2026-06-11): Si el usuario es supervisor (Daniel), el Activities
    Bot le da acceso a tools de gerencia (`add_activity_for_collaborator`).
    Así Daniel puede asignar actividades a OTROS desde su chat del bot.
    """
    if mode == "data":
        base = [t for t in TOOLS if t["name"] in DATA_TOOL_NAMES]
        # Web search nativo de Anthropic — Claude puede investigar noticias
        # actuales para responder preguntas de proyección/escenarios.
        base.append(WEB_SEARCH_TOOL_DEF)
        return base
    if mode == "activities":
        allowed = set(TRACKER_TOOL_NAMES)
        email_l = (user_email or "").lower()
        if email_l in SUPERVISORS_ONLY_EMAILS:
            allowed |= SUPERVISOR_EXTRA_TOOLS_ACTIVITIES
        # Gerencia con calendario habilitado (Daniel + Gabriela Sánchez) puede
        # agendar reuniones y recordatorios aunque no sea "supervisor puro".
        if email_l in {e.lower() for e in core_config.CALENDAR_SYNC_USERS}:
            allowed |= SCHEDULING_TOOLS
        return [t for t in TOOLS if t["name"] in allowed]
    return TOOLS  # full (CLI debug)


def ask(
    question: str,
    user_email: str | None = None,
    mode: str = "full",
    verbose: bool = False,
    history: list[dict] | None = None,
) -> str:
    """Procesa una pregunta y devuelve la respuesta de Claude.

    Args:
        question: pregunta en lenguaje natural
        user_email: email del usuario (para tools del tracker — scope per-user)
        mode: 'data' (Data Bot), 'activities' (Activities Bot), 'full' (CLI debug)
        verbose: si True, imprime las llamadas a herramientas (stderr)
        history: lista de turns previos [{role: user/assistant, content: str}]
            para conversación multi-turn. None = conversación nueva.
    """
    import time
    import anthropic as _anthropic_mod

    client = Anthropic()
    # Construir messages: history previo + el nuevo turn del usuario
    messages: list = list(history or [])
    messages.append({"role": "user", "content": question})
    tools = _tools_for_mode(mode, user_email=user_email)
    system_text = _system_prompt(mode, user_email)

    def _create_with_retry():
        """Reintenta hasta 2 veces si 429. Backoff exponencial 30s -> 60s."""
        last_err = None
        for attempt in range(3):
            try:
                return client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=[
                        {
                            "type": "text",
                            "text": system_text,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    tools=tools,
                    messages=messages,
                )
            except _anthropic_mod.RateLimitError as e:
                last_err = e
                if attempt < 2:
                    wait = 30 * (attempt + 1)
                    print(
                        f"  [429 rate limit, retry {attempt + 1}/2 en {wait}s]",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise last_err  # unreachable

    for i in range(MAX_ITERATIONS):
        response = _create_with_retry()

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text" and block.text:
                    return block.text
            return "(no obtuve respuesta de texto)"

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list = []
            for block in response.content:
                if block.type == "tool_use":
                    if verbose:
                        print(
                            f"  [tool] {block.name}({json.dumps(block.input)[:100]}...)",
                            file=sys.stderr,
                        )
                    result = _call_tool(
                        block.name, block.input, user_email=user_email
                    )
                    if verbose:
                        print(f"  [result] {result[:200]}...", file=sys.stderr)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            # Truncamos a 2500 chars para no inflar tokens en iteraciones
                            "content": result[:2500],
                        }
                    )
            messages.append({"role": "user", "content": tool_results})
            continue

        return f"(stop_reason inesperado: {response.stop_reason})"

    return "(se alcanzó el límite de iteraciones)"


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(sys.argv) < 2:
        print("Uso: python ask_agent.py [--verbose] 'tu pregunta'")
        return 1

    args = sys.argv[1:]
    verbose = False
    if args and args[0] == "--verbose":
        verbose = True
        args = args[1:]
    if not args:
        print("Falta la pregunta")
        return 1

    question = " ".join(args)
    print(ask(question, verbose=verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
