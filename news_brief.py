"""Daily news brief — investigación automática de contexto actual.

Cada día 6 AM EC, el scheduler llama `generate_brief()` que usa Claude API
con web_search nativo de Anthropic para investigar 3 áreas relevantes para
Biodegradables Ecuador:

1. Economía Ecuador (BCE, El Comercio, Primicias, gobierno)
2. Geopolítica / supply chains (Canal Panamá, rutas marítimas, China)
3. Sector empaques biodegradables (regulaciones, competencia, tendencias)

El resultado se guarda en `~/.claude-agent/daily_news_brief.json`.

Cuando Daniel/Gabriela hacen preguntas de proyección al Data Bot, el system
prompt incluye automáticamente el brief actual via `format_brief_for_prompt()`.
Así Claude SIEMPRE tiene el contexto fresco sin tener que buscar en tiempo
real (más rápido + menos costo por query).

Phase I (2026-05-31).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from anthropic import Anthropic

import core_config

LOCAL_TZ = timezone(timedelta(hours=-5))
STATE_PATH = Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "daily_news_brief.json"
MODEL = "claude-sonnet-4-6"


def _ensure_dir() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def load_brief() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def is_brief_fresh(max_age_hours: int = 30) -> bool:
    """Brief es 'fresh' si tiene menos de N horas. Default 30 (cubre el día
    aunque el job de las 6 AM se retrase un poco)."""
    brief = load_brief()
    ts = brief.get("generated_at")
    if not ts:
        return False
    try:
        gen = datetime.fromisoformat(ts)
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=LOCAL_TZ)
        age = _now() - gen
        return age < timedelta(hours=max_age_hours)
    except (ValueError, TypeError):
        return False


PROMPT_TEMPLATE = """Sos un analista económico. Hoy es {hoy}.

Hacé una investigación con web_search de las noticias relevantes para {empresa_desc}.
Investigá EXCLUSIVAMENTE noticias publicadas en los últimos 7 días.

Cubrí estas 3 áreas:

1. **Economía Ecuador**: indicadores macro recientes (PIB, inflación, tipo
   de cambio paralelo si aplica, deuda externa), anuncios del gobierno o BCE,
   política monetaria, eventos sociales/políticos que afecten al comercio
   (paros, huelgas, elecciones).

2. **Geopolítica / supply chains globales**: eventos que puedan afectar
   imports desde China/Asia (Canal de Panamá, rutas marítimas Pacífico/Suez,
   tarifas o sanciones internacionales, costos de flete contenedor, conflictos
   en zonas productoras).

3. **Sector {sector_nombre}**: regulaciones aplicables en Ec o LATAM,
   tendencias del mercado / consumidor, anuncios de competidores grandes en la
   región que afecten al sector.

Para cada área devolveme 3-5 puntos concisos (cada uno 1-2 líneas) con la
fuente y la fecha entre paréntesis. Si en alguna área no encontrás nada
relevante en los últimos 7 días, escribí una entrada única que diga
"Sin novedades relevantes en los últimos 7 días".

CRÍTICO: NO inventes. Solo lo que confirmás con resultados de web_search.

Formato de salida: SOLO JSON, sin markdown, sin texto antes ni después,
empezando directo con {{. Estructura:

{{
  "economia_ecuador": ["punto 1 con fuente y fecha", "punto 2 ..."],
  "geopolitica_supply": ["..."],
  "sector_industria": ["..."]
}}"""


def generate_brief() -> dict[str, Any]:
    """Genera el brief diario usando Claude + web search.

    Returns el dict guardado.
    Raises si la API call falla.
    """
    client = Anthropic()
    _cities = " y ".join(core_config.SUCURSAL_NAMES.values()) or "Ecuador"
    prompt = PROMPT_TEMPLATE.format(
        hoy=_now().strftime("%A %d de %B de %Y"),
        empresa_desc=(
            f"una empresa de {core_config.COMPANY_SECTOR} en Ecuador "
            f"(sucursales en {_cities})"
        ),
        sector_nombre=core_config.COMPANY_SECTOR,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        tools=[
            {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 10,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    final_text = ""
    for block in response.content:
        if getattr(block, "type", "") == "text":
            final_text += getattr(block, "text", "")

    # Parsear JSON tolerante
    brief_data: dict[str, Any]
    try:
        start = final_text.find("{")
        end = final_text.rfind("}") + 1
        if start < 0 or end <= start:
            raise ValueError("No JSON found")
        brief_data = json.loads(final_text[start:end])
    except (json.JSONDecodeError, ValueError) as e:
        brief_data = {
            "error": f"Could not parse JSON from Claude response: {e}",
            "raw_preview": final_text[:1000],
        }

    brief_data["generated_at"] = _now().isoformat(timespec="seconds")

    _ensure_dir()
    STATE_PATH.write_text(
        json.dumps(brief_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return brief_data


def format_brief_for_prompt() -> str:
    """Devuelve el brief formateado para inyectar al system prompt de Data Bot.

    Devuelve '' si no hay brief disponible o si tiene error.
    """
    brief = load_brief()
    if not brief or brief.get("error") or "generated_at" not in brief:
        return ""

    sections = []
    sec_def = [
        ("economia_ecuador", "🇪🇨 Economía Ecuador"),
        ("geopolitica_supply", "🌎 Geopolítica / supply chains"),
        ("sector_industria", f"📦 Sector {core_config.COMPANY_SECTOR}"),
    ]
    for key, label in sec_def:
        points = brief.get(key) or []
        if points:
            sec_text = f"**{label}:**\n" + "\n".join(f"  - {p}" for p in points)
            sections.append(sec_text)

    if not sections:
        return ""

    return (
        f"\n\nCONTEXTO ACTUAL (news brief — generado {brief['generated_at']}):\n"
        + "\n\n".join(sections)
        + "\n\nUsá este contexto si la pregunta del usuario es sobre proyecciones, "
          "escenarios, o cualquier cosa donde las noticias actuales sean relevantes. "
          "Si el contexto no aplica a la pregunta, ignoralo."
    )


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    cmd = sys.argv[1] if len(sys.argv) >= 2 else "show"

    if cmd == "generate":
        print("Generando brief... (puede tardar 30-60s)")
        result = generate_brief()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif cmd == "show":
        brief = load_brief()
        if not brief:
            print("(no hay brief generado todavía — corre `python news_brief.py generate`)")
        else:
            print(json.dumps(brief, indent=2, ensure_ascii=False))
    elif cmd == "format":
        print(format_brief_for_prompt() or "(empty — no brief or no sections)")
    elif cmd == "fresh":
        print(f"Brief fresh? {is_brief_fresh()}")
    else:
        print("Comandos: generate | show | format | fresh")
