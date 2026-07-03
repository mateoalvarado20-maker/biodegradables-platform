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


def _parse_date(s: str | None, default: date | None = None) -> date:
    """Parsea fecha ISO YYYY-MM-DD. Si está vacía, usa default o hoy."""
    if not s:
        return default or _hoy_ec()
    try:
        return date.fromisoformat(s.strip())
    except ValueError:
        return default or _hoy_ec()


# ============ Tracker helpers (Phase C) ============
# F4.2: TRACKER_EMAIL_FROM / TRACKER_EMAIL_TO_DEFAULT / _load_collaborators /
# COLLABORATORS viven en team_reports (capa de reportes del equipo) y se
# re-importan más abajo junto con el resto del bloque extraído.
from team_reports import (  # noqa: F401
    COLLABORATORS,
    TRACKER_EMAIL_FROM,
    TRACKER_EMAIL_TO_DEFAULT,
    _load_collaborators,
)


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
    # Resolución EN CALIENTE (F4.2): leer el directorio al momento del uso —
    # env/config cambiados aplican sin restart, sin sensibilidad al orden de
    # carga de módulos.
    collaborators = _load_collaborators()
    registered = {e.strip().lower() for e in collaborators.values() if e}
    exact: dict[str, set[str]] = {}
    tokens: dict[str, set[str]] = {e: set() for e in registered}

    def _add_exact(term: str, email: str) -> None:
        nt = _norm_name(term)
        if nt:
            exact.setdefault(nt, set()).add(email)
            tokens[email].update(nt.split())

    # Aliases de KNOWN_COLLABORATORS (incluye 'gabriela'→gsanchez@, 'gye'→info@)
    for alias, email in collaborators.items():
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


# F4.2 (VER-IA 2026-07-03): los reportes/correos del equipo viven en
# team_reports.py. Re-export de compatibilidad — el código nuevo importa
# desde team_reports; NO agregar funciones de reporte aquí.
from team_reports import (  # noqa: F401
    ASISTENTE_EMAILS,
    CAJA_CHICA_ALERTA_JOSE,
    CIERRE_CAJA_CC_DEFAULT,
    CIERRE_CAJA_TO_DEFAULT,
    CONSOLIDATED_DAILY_CC_DEFAULT,
    CONSOLIDATED_DAILY_TO_DEFAULT,
    GYE_ASISTENTE1_EMAIL,
    GYE_ROTATIVOS_SABADO,
    JOSE_EMAIL_CONS,
    SUPERVISORS_ONLY_EMAILS,
    _CHOFER_SUCURSAL,
    _asistente_column_html,
    _asistentes_section_html,
    _ausencia_rotativa_block_html,
    _build_fondo_caja_html,
    _build_weekly_comparison,
    _cierre_caja_html,
    _classify_dailies,
    _collaborator_block_html_v2,
    _consolidated_daily_summary_html,
    _executive_summary_table,
    _gye_sin_reporte_dia,
    _hoy_ec,
    _jose_consolidated_block_html,
    _problemas_section,
    _proyectos_pendientes_section,
    _resolve_supervisors,
    _send_apertura_caja_email,
    _send_cierre_caja_email,
    _send_confirmacion_cierre_email,
    _send_consolidated_daily_summary,
    _send_daily_summary_email,
    _send_weekly_summary_email,
    _sin_marcar_section,
    _summary_html,
    _team_collaborator_emails,
    _team_workload_html,
    _ultimo_sabado,
    _weekly_summary_html,
    _workload_rollup,
    _workload_text_for_chat,
    send_saturday_recap_summary,
    send_team_workload_summary,
)


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
            f"Desglose de ventas por sucursal ({core_config.COMPANY_SUCURSALES_DESC}) "
            "en un día o rango. La sucursal se deduce del prefijo del documento: "
            + ", ".join(f"{p}={s}" for s, p in core_config.DOC_PREFIXES.items())
            + "."
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
    # Contacto admin (quien vincula usuarios) = el analista del tenant.
    _admin_name, _admin_email = next(
        ((p["name"], e) for e, p in core_config.PEOPLE.items()
         if p.get("role") == "analista"),
        ("el administrador del sistema", ""),
    )

    # Cuenta sin vincular: el AAD del usuario no está registrado, así que el bot
    # no sabe quién es ni qué permisos tiene (un supervisor como Daniel aparece
    # acá si su AAD nunca se mapeó → pierde las tools de delegación). Damos un
    # mensaje claro y accionable en vez de improvisar o "perder" la petición.
    no_id_block = ""
    if es_no_identificado:
        no_id_block = f"""

⚠️ CUENTA NO VINCULADA:
El AAD de este usuario no está registrado, así que NO sé con certeza quién es
ni qué puede hacer. NO asumas que es un colaborador ni un supervisor.
Si pide asignar/delegar algo a otra persona, o marcar actividades, respondé:
"Tu cuenta todavía no está vinculada al sistema, por eso no puedo registrar ni
delegar nada de forma segura. Escribile a {_admin_name} ({_admin_email})
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

    return f"""Eres el asistente de tracking de actividades de {core_config.COMPANY_NAME}.

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
    # Supervisores (gerencia) y un colaborador de ejemplo, desde core_config —
    # así los ejemplos del prompt quedan correctos para cualquier tenant.
    _supervisores = " y ".join(
        p["name"] for p in core_config.PEOPLE.values()
        if p.get("supervisor") or p.get("role") == "gerente_comercial"
    ) or "la gerencia"
    _ej_colab = next(
        (p["name"].split()[0] for p in core_config.PEOPLE.values()
         if p.get("role") == "analista"),
        "un colaborador",
    )
    base_prompt = f"""Eres el asistente de datos comerciales de {core_config.COMPANY_NAME}.

Respondes preguntas sobre la operación comercial usando 2 fuentes:
- **Contifico** (ERP, source of truth en tiempo real para ventas, clientes, vendedores)
- **HubSpot** (CRM para leads, deals, pipeline)

Tu trabajo: interpretar la pregunta, llamar las herramientas correctas, y devolver
una respuesta clara y breve en español.

CONTEXTO:
- Hoy es {now_ec.strftime("%A %d de %B de %Y, %H:%M")} (Ecuador, UTC-5)
- Empresa: {core_config.COMPANY_NAME} — {core_config.COMPANY_SECTOR}
- Sucursales: {core_config.COMPANY_SUCURSALES_DESC}
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
  {core_config.COMPANY_NAME} no importa de Yemen), explicalo y no infles ajustes
- Para factores muy especulativos ("y si baja el precio del litio"), aclarar
  que es razonamiento sin data específica

Ejemplos:
- "proyecta junio" → forecast_sales_for_month(2026, 6) + respuesta directa
- "proyecta junio si cierran Panamá" → forecast + analyze_product_mix(['china','asia']) + razonamiento
- "qué % de mi mix es PLA" → analyze_product_mix(['PLA','pla','poliláctico'])

GESTIÓN DE EQUIPO (Phase E — solo gerencia):
Como {_supervisores} son supervisores, pueden asignar tareas y recordatorios
a otros colaboradores. Patrones típicos:

- "Añade a {_ej_colab} a esta semana hacer un bot de WhatsApp"
  → llamá `list_team_collaborators` (para resolver "{_ej_colab}" → email)
  → llamá `add_activity_for_collaborator` con target_user, activity_id (slug
     que vos generás), nombre legible, tipo='unica' o 'semanal'
  → confirmá: "Listo, le agregué a {_ej_colab}: 'Bot WhatsApp...'. Le aparecerá
     en su próximo check-in (lun 4:30 PM)."

- "Recordale a {_ej_colab} un día antes que el 15 entrega el reporte mensual"
  → resolvé {_ej_colab} → email
  → calculá la fecha: si entrega el 15, el recordatorio va el 14 a una hora
     razonable (default 8 AM Ecuador)
  → llamá `schedule_reminder_for_collaborator` con send_at en ISO
  → confirmá: "Listo, le programé recordatorio para el 14 a las 8 AM:
     'Mañana entregás el reporte mensual de ventas'"

- "Qué recordatorios le mandé a {_ej_colab}?"
  → llamá `list_pending_reminders` con filtro target_user='{_ej_colab}'
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
# Fase 1 (de-hardcode): ahora se deriva de core_config.PEOPLE (single source,
# tenant-overridable). Los valores legacy de Biodegradables son idénticos.
EMAIL_TO_NAME = core_config.EMAIL_TO_NAME


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

# F2.3 (VER-IA 2026-07-02): tools que pertenecen a un MÓDULO del catálogo del
# tenant (core_config.MODULES). Módulo apagado = sus tools desaparecen de
# TODOS los modos (el bot ni siquiera le cuenta a Claude que existen).
# `list_team_collaborators` (directorio) y `web_search` quedan sin gatear.
MODULE_TOOL_NAMES = {
    "commercial": {
        "get_ventas_dia", "get_ventas_rango", "get_ventas_por_ciudad",
        "get_top_vendedores", "get_top_clientes", "get_cumplimiento_mes",
        "forecast_sales_for_month", "analyze_product_mix",
    },
    "cobranzas": {"get_saldos_pendientes_clientes"},
    "marketing": {
        "get_hubspot_leads_ayer", "get_hubspot_leads_promedio_7d",
        "get_hubspot_deals_ganados_ayer", "get_hubspot_pipeline_abierto",
    },
    "activities": TRACKER_TOOL_NAMES | {
        "list_team_workload",
        "add_activity_for_collaborator",
        "set_activity_priority_for_collaborator",
        "schedule_reminder_for_collaborator",
        "list_pending_reminders",
    },
    "calendar": {"create_calendar_meeting_for_collaborator"},
}


def _disabled_tool_names() -> set:
    """Tools de módulos apagados en la config del tenant."""
    out: set = set()
    for mod, names in MODULE_TOOL_NAMES.items():
        if not core_config.module_enabled(mod):
            out |= names
    return out


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
            for alias, email in _load_collaborators().items():
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
    # F2.3: los módulos apagados del tenant quitan sus tools de TODOS los modos.
    disabled = _disabled_tool_names()
    if mode == "data":
        base = [
            t for t in TOOLS
            if t["name"] in DATA_TOOL_NAMES and t["name"] not in disabled
        ]
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
        return [t for t in TOOLS if t["name"] in allowed - disabled]
    return [t for t in TOOLS if t["name"] not in disabled]  # full (CLI debug)


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
                resp = client.messages.create(
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
                # F3 (VER-IA): metering por iteración del loop de tools —
                # antes response.usage se DESCARTABA (auditoría H12) y el
                # gasto de IA era invisible hasta la factura. Nunca lanza.
                import llm_usage
                llm_usage.record(f"ask_agent:{mode}", MODEL, resp.usage)
                return resp
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
