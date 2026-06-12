"""Agente conversacional con Claude para el bot de Teams.

Recibe una pregunta en lenguaje natural y la responde usando tools que
consultan datos en vivo de Contifico API y del dispatch_state local.

Cuando Anthropic agrega prompt caching del system prompt automáticamente
(cache_control ephemeral), las respuestas posteriores son más rápidas y
económicas.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from anthropic import Anthropic

import contifico_client
import dispatch_state
import pbi_query
from pbi_query import PBINotConfigured

logger = logging.getLogger("chat_agent")

MODEL = "claude-sonnet-4-5"
MAX_TOKENS = 2000
LOCAL_TZ = timezone(timedelta(hours=-5))


# ============ Helpers de fecha en lenguaje natural ============
def _today_ec() -> date:
    return datetime.now(LOCAL_TZ).date()


def _parse_iso_date(s: str) -> date:
    """Acepta 'YYYY-MM-DD' o 'DD/MM/YYYY' o 'today'/'yesterday'."""
    if not s:
        return _today_ec()
    s = s.strip().lower()
    if s in ("today", "hoy"):
        return _today_ec()
    if s in ("yesterday", "ayer"):
        return _today_ec() - timedelta(days=1)
    if s in ("anteayer",):
        return _today_ec() - timedelta(days=2)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Fecha no reconocida: {s}")


# ============ Helpers ============
def _ciudad_origen(documento: str) -> str:
    """Deduce la ciudad de emisión del prefijo del documento.
    001-001 → Guayaquil, 001-002 → Quito."""
    if not documento or len(documento) < 7:
        return "Desconocida"
    prefix = documento[:7]
    if prefix == "001-001":
        return "Guayaquil"
    if prefix == "001-002":
        return "Quito"
    return "Desconocida"


# ============ Tools ============
TOOLS = [
    {
        "name": "ventas_por_rango",
        "description": (
            "Devuelve ventas totales (facturas tipo FAC) entre dos fechas, "
            "incluyendo cantidad de facturas, monto total, monto promedio por "
            "factura, y top 5 clientes del periodo. Las fechas pueden ser "
            "'today', 'yesterday', 'anteayer' o formato 'YYYY-MM-DD' / 'DD/MM/YYYY'. "
            "Excluye facturas anuladas."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {
                    "type": "string",
                    "description": "Fecha inicial del rango (incluida).",
                },
                "fecha_final": {
                    "type": "string",
                    "description": "Fecha final del rango (incluida).",
                },
            },
            "required": ["fecha_inicial", "fecha_final"],
        },
    },
    {
        "name": "buscar_facturas_cliente",
        "description": (
            "Busca facturas de un cliente específico en los últimos N días. "
            "Devuelve hasta 20 facturas con número, fecha, total y línea de "
            "transporte si aplica."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cliente_substring": {
                    "type": "string",
                    "description": "Parte del nombre del cliente (case-insensitive). Ej. 'MULANO', 'Espinoza'.",
                },
                "dias_atras": {
                    "type": "integer",
                    "description": "Cuántos días hacia atrás buscar (default 30, máximo 180).",
                },
            },
            "required": ["cliente_substring"],
        },
    },
    {
        "name": "estado_despachos",
        "description": (
            "Devuelve el estado de despacho de las facturas marcadas (vía bot "
            "Teams o CLI). Útil para saber cuántos pedidos están confirmados, "
            "rechazados o parciales. Opcionalmente filtra por status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status_filter": {
                    "type": "string",
                    "enum": ["OK", "NO", "PARCIAL", "TODOS"],
                    "description": "Filtrar por status. 'TODOS' devuelve todo.",
                },
            },
        },
    },
    {
        "name": "ventas_por_ciudad",
        "description": (
            "Devuelve ventas agregadas en un rango de fechas DIVIDIDAS por "
            "ciudad de origen (Quito vs Guayaquil). Útil para preguntas como "
            "'cuánto facturó Quito esta semana' o 'comparame ventas Quito vs "
            "Guayaquil del mes'. Se basa en el prefijo del número de factura: "
            "001-001=Guayaquil, 001-002=Quito."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha_inicial": {"type": "string"},
                "fecha_final": {"type": "string"},
            },
            "required": ["fecha_inicial", "fecha_final"],
        },
    },
    {
        "name": "cartera_total",
        "description": (
            "Devuelve la cartera total, cartera vencida, no vencida y el "
            "porcentaje de vencida. Usa datos en vivo de Power BI."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cartera_por_antiguedad",
        "description": (
            "Devuelve la cartera vencida desglosada por buckets de antigüedad: "
            "1-30 días, 31-60, 61-90 y +90 días. Usa Power BI."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "top_deudores",
        "description": (
            "Devuelve los top N clientes con más deuda vencida, opcionalmente "
            "filtrado por ciudad (UIO=Quito o GYE=Guayaquil)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ciudad": {
                    "type": "string",
                    "description": "Opcional: 'UIO'/'Quito' o 'GYE'/'Guayaquil'. Si no se pasa, devuelve todos.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Cuántos top devolver (default 10, máx 30).",
                },
            },
        },
    },
    {
        "name": "cumplimiento_mes",
        "description": (
            "Devuelve el progreso de cumplimiento del mes en curso: ventas "
            "acumuladas MTD, meta mensual, % de cumplimiento, brecha pendiente "
            "y ritmo diario necesario para llegar."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _tool_ventas_por_rango(fecha_inicial: str, fecha_final: str) -> dict:
    fi = _parse_iso_date(fecha_inicial)
    ff = _parse_iso_date(fecha_final)
    if fi > ff:
        fi, ff = ff, fi
    docs = contifico_client.get_documentos(fi, ff, tipo="FAC")
    docs_validos = [d for d in docs if not d.get("anulado")]
    total = sum(float(d.get("total") or 0) for d in docs_validos)
    n = len(docs_validos)
    avg = total / n if n else 0.0

    # Top clientes
    por_cliente: dict[str, float] = defaultdict(float)
    for d in docs_validos:
        persona = d.get("persona") or {}
        nombre = persona.get("razon_social") or "—"
        por_cliente[nombre] += float(d.get("total") or 0)
    top = sorted(por_cliente.items(), key=lambda x: -x[1])[:5]

    return {
        "fecha_inicial": fi.isoformat(),
        "fecha_final": ff.isoformat(),
        "num_facturas": n,
        "monto_total_usd": round(total, 2),
        "monto_promedio_usd": round(avg, 2),
        "top_clientes": [{"cliente": k, "total_usd": round(v, 2)} for k, v in top],
    }


def _tool_buscar_facturas_cliente(cliente_substring: str, dias_atras: int = 30) -> dict:
    dias = max(1, min(180, dias_atras))
    ff = _today_ec()
    fi = ff - timedelta(days=dias)
    docs = contifico_client.get_documentos(fi, ff, tipo="FAC")
    needle = cliente_substring.lower()
    matches = []
    for d in docs:
        if d.get("anulado"):
            continue
        persona = d.get("persona") or {}
        nombre = (persona.get("razon_social") or "").lower()
        if needle in nombre:
            matches.append({
                "documento": d.get("documento"),
                "fecha": d.get("fecha_emision"),
                "cliente": persona.get("razon_social"),
                "total_usd": float(d.get("total") or 0),
                "estado": d.get("estado"),
            })
    matches = sorted(matches, key=lambda x: x.get("fecha", ""), reverse=True)[:20]
    return {
        "cliente_buscado": cliente_substring,
        "dias_atras": dias,
        "encontradas": len(matches),
        "facturas": matches,
    }


def _tool_ventas_por_ciudad(fecha_inicial: str, fecha_final: str) -> dict:
    """Igual que ventas_por_rango pero dividido por ciudad de origen."""
    fi = _parse_iso_date(fecha_inicial)
    ff = _parse_iso_date(fecha_final)
    if fi > ff:
        fi, ff = ff, fi
    docs = contifico_client.get_documentos(fi, ff, tipo="FAC")
    docs_validos = [d for d in docs if not d.get("anulado")]

    por_ciudad: dict[str, dict[str, Any]] = {
        "Quito": {"num_facturas": 0, "total_usd": 0.0, "clientes": defaultdict(float)},
        "Guayaquil": {"num_facturas": 0, "total_usd": 0.0, "clientes": defaultdict(float)},
        "Desconocida": {"num_facturas": 0, "total_usd": 0.0, "clientes": defaultdict(float)},
    }
    for d in docs_validos:
        ciudad = _ciudad_origen(d.get("documento", ""))
        bucket = por_ciudad[ciudad]
        total = float(d.get("total") or 0)
        bucket["num_facturas"] += 1
        bucket["total_usd"] += total
        persona = d.get("persona") or {}
        nombre = persona.get("razon_social") or "—"
        bucket["clientes"][nombre] += total

    # Convertir top clientes a lista ordenada
    out_por_ciudad = {}
    for ciudad, data in por_ciudad.items():
        if data["num_facturas"] == 0 and ciudad == "Desconocida":
            continue
        top = sorted(data["clientes"].items(), key=lambda x: -x[1])[:3]
        out_por_ciudad[ciudad] = {
            "num_facturas": data["num_facturas"],
            "total_usd": round(data["total_usd"], 2),
            "top_3_clientes": [
                {"cliente": k, "total_usd": round(v, 2)} for k, v in top
            ],
        }

    total_general = sum(v["total_usd"] for v in out_por_ciudad.values())
    return {
        "fecha_inicial": fi.isoformat(),
        "fecha_final": ff.isoformat(),
        "total_usd_general": round(total_general, 2),
        "por_ciudad": out_por_ciudad,
    }


def _tool_cartera_total() -> dict:
    try:
        return pbi_query.cartera_total()
    except PBINotConfigured as e:
        return {"_error": "Power BI no configurado todavía", "_detalle": str(e)}


def _tool_cartera_por_antiguedad() -> dict:
    try:
        return pbi_query.cartera_por_antiguedad()
    except PBINotConfigured as e:
        return {"_error": "Power BI no configurado todavía", "_detalle": str(e)}


def _tool_top_deudores(ciudad: str = "", limit: int = 10) -> dict:
    limit = max(1, min(30, limit))
    try:
        rows = pbi_query.top_deudores(ciudad=ciudad, limit=limit)
        return {"ciudad": ciudad or "Todas", "limit": limit, "deudores": rows}
    except PBINotConfigured as e:
        return {"_error": "Power BI no configurado todavía", "_detalle": str(e)}


def _tool_cumplimiento_mes() -> dict:
    try:
        return pbi_query.cumplimiento_mes()
    except PBINotConfigured as e:
        return {"_error": "Power BI no configurado todavía", "_detalle": str(e)}


def _tool_estado_despachos(status_filter: str = "TODOS") -> dict:
    state = dispatch_state.load()
    out = []
    for factura, rec in state.items():
        status = rec.get("status", "")
        if status_filter != "TODOS" and status != status_filter:
            continue
        out.append({
            "factura": factura,
            "status": status,
            "razon": rec.get("razon", ""),
            "marcado_por": rec.get("marcado_por", ""),
            "marcado_en": rec.get("marcado_en", ""),
        })
    out.sort(key=lambda x: x.get("marcado_en", ""), reverse=True)
    return {
        "filtro": status_filter,
        "total": len(out),
        "registros": out[:50],
    }


def _dispatch_tool(name: str, args: dict) -> dict:
    if name == "ventas_por_rango":
        return _tool_ventas_por_rango(**args)
    if name == "buscar_facturas_cliente":
        return _tool_buscar_facturas_cliente(**args)
    if name == "estado_despachos":
        return _tool_estado_despachos(**args)
    if name == "ventas_por_ciudad":
        return _tool_ventas_por_ciudad(**args)
    if name == "cartera_total":
        return _tool_cartera_total()
    if name == "cartera_por_antiguedad":
        return _tool_cartera_por_antiguedad()
    if name == "top_deudores":
        return _tool_top_deudores(**args)
    if name == "cumplimiento_mes":
        return _tool_cumplimiento_mes()
    raise ValueError(f"Tool desconocida: {name}")


# ============ System prompt ============
def _system_prompt() -> str:
    hoy = _today_ec()
    return f"""Eres el asistente interno de Biodegradables Ecuador, una empresa que vende
empaques biodegradables en Ecuador. Estás hablando con uno de los tres usuarios autorizados:

- Daniel Sánchez (dsanchez@) — gerente general
- Gabriela Sánchez (gsanchez@) — gerente comercial
- Mateo Alvarado (malvarado@) — comercial / encargado técnico

CONTEXTO IMPORTANTE:
- Hoy es {hoy.isoformat()} ({hoy.strftime("%A %d de %B de %Y")})
- Operan en Quito (UIO, prefijo factura 001-002) y Guayaquil (GYE, prefijo 001-001)
- ERP fuente: Contifico (POS / facturación)
- Moneda: USD
- Timezone: Ecuador UTC-5 (sin DST)

TUS HERRAMIENTAS:

Datos en vivo de Contifico:
1. `ventas_por_rango(fecha_inicial, fecha_final)` — total general + top clientes
2. `ventas_por_ciudad(fecha_inicial, fecha_final)` — total dividido por Quito vs Guayaquil
3. `buscar_facturas_cliente(cliente_substring, dias_atras)` — busca facturas de un cliente
4. `estado_despachos(status_filter)` — estado de pedidos marcados en el bot

Datos en vivo de Power BI (cartera, cobranzas, meta):
5. `cartera_total()` — total adeudado y % vencida
6. `cartera_por_antiguedad()` — buckets 1-30, 31-60, 61-90, +90 días
7. `top_deudores(ciudad, limit)` — top clientes con más deuda; ciudad opcional
8. `cumplimiento_mes()` — ventas MTD, meta, % cumplimiento, ritmo necesario

Si una tool de PBI devuelve `_error: "Power BI no configurado todavía"`, decile al
usuario que necesita completar el setup en PBI Admin Portal (agregar el service
principal al workspace) — no inventes los datos.

GUÍA DE COMPORTAMIENTO:
- Responde directo y profesional, en español
- Para preguntas vagas sobre tiempo ("este mes", "esta semana"), interpreta sobre
  la fecha de hoy
- Cuando uses tools, NO menciones los nombres técnicos — solo presenta los datos
- Usa formato markdown: negritas para resaltar números, viñetas para listas
- Limita listas a 5-10 elementos máximo; si hay más, mencionalo
- Si te piden algo que NO podés hacer con tus tools (ej. cartera vencida,
  cobranzas, datos de Power BI), decilo claramente y sugerí alternativas
- NO inventes datos. Si una tool devuelve 0, decílo
- Para fechas pasadas en lenguaje natural ("el lunes", "hace 3 días"), calculá
  la fecha exacta basada en hoy ({hoy.isoformat()})

FORMATO DE NÚMEROS:
- Montos: $1,234.56 (con coma para miles, punto para decimales)
- Porcentajes: 12.3%
- Sé conciso, no repitas el contexto que el usuario ya tiene"""


# ============ API pública ============
def reply_to(message: str, user_email: str = "") -> str:
    """Procesa una pregunta y devuelve la respuesta del agente.

    Stateless por ahora: cada llamada es independiente, sin historial.
    """
    client = Anthropic()
    messages = [{"role": "user", "content": message}]

    # Loop de tool use
    for _ in range(10):  # cap de iteraciones para evitar infinitos
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=[{
                "type": "text",
                "text": _system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            tools=TOOLS,
            messages=messages,
        )

        # Si terminó normal sin pedir más tools
        if response.stop_reason == "end_turn":
            text = ""
            for block in response.content:
                if block.type == "text":
                    text += block.text
            return text.strip() or "(sin respuesta)"

        # Procesar tool_use
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = _dispatch_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        })
                    except Exception as e:
                        logger.exception("Error en tool %s: %s", block.name, e)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "is_error": True,
                            "content": f"Error ejecutando tool: {e}",
                        })
            messages.append({"role": "user", "content": tool_results})
            continue

        # Otro stop_reason inesperado
        logger.warning("stop_reason inesperado: %s", response.stop_reason)
        break

    return "Lo siento, no pude resolver tu pregunta. Intenta reformularla."
