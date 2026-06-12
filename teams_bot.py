"""Teams Bot backend — hostea DOS bots en el mismo App Service.

Phase D (2026-05-30): se separa en 2 bots con identidades distintas:

1. **Data Bot** (`biodegradables-data-bot`, APP_ID 8ef9d83a-...)
   - Endpoint: /api/messages
   - Acceso: solo gerencia (Daniel + Gabriela) + Mateo
   - Tools: Contifico (ventas) + HubSpot (CRM)

2. **Activities Bot** (`biodegradables-activities-bot`, APP_ID bc908e6c-...)
   - Endpoint: /api/activities/messages
   - Acceso: cualquier colaborador del tenant
   - Funciones: check-in diario con Adaptive Card, marcado de actividades,
     envío de resumen al supervisor
   - Scheduler: Lun-Vie 16:30 EC + Sáb 12:30 EC

Comparten infraestructura (App Service, env vars Graph, código base) pero
cada uno tiene su propia App Registration y credenciales.

Variables de entorno:
- MICROSOFT_APP_ID / _PASSWORD / _TENANT_ID — credenciales del Data Bot
- ACTIVITIES_APP_ID / _PASSWORD — credenciales del Activities Bot
- ANTHROPIC_API_KEY, HUBSPOT_TOKEN, CONTIFICO_API_TOKEN — APIs
- TRACKER_EMAIL_TO — destinatarios del resumen (CSV)
- BOT_ALLOWED_USERS_DATA — allowlist del Data Bot (CSV)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import (
    Activity,
    ActivityTypes,
    Attachment,
    ConversationReference,
)
from fastapi import FastAPI, HTTPException, Request, Response
import pytz

import ask_agent
import activity_state
import contifico_client
import core_config
import conversation_history
import monthly_recap
import news_brief
import reminders
import safe_json
import send_ledger
from ask_agent import _send_daily_summary_email, _send_weekly_summary_email
from datetime import date as _date_cls
import re as _re
import unicodedata as _unicodedata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("teams_bot")

# ===== Configuración de los dos bots =====
DATA_APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
DATA_APP_PWD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
APP_TENANT_ID = os.environ.get("MICROSOFT_APP_TENANT_ID", "")
APP_TYPE = os.environ.get("MICROSOFT_APP_TYPE", "SingleTenant")

# Fase 2 (auditoría C5): token admin PROPIO, separado del secret OAuth del
# bot. Mientras ADMIN_API_TOKEN no esté seteado en el App Service, cae al
# password del bot (compat con los scripts de testing existentes) — setearlo
# y rotar es la migración recomendada.
ADMIN_API_TOKEN = os.environ.get("ADMIN_API_TOKEN", "") or DATA_APP_PWD


def _require_admin(request: "Request") -> None:
    """Valida el header X-Admin-Token (comparación constante, fail-closed)."""
    import hmac
    token = request.headers.get("x-admin-token", "")
    if not ADMIN_API_TOKEN or not hmac.compare_digest(token, ADMIN_API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid admin token")

ACTIVITIES_APP_ID = os.environ.get("ACTIVITIES_APP_ID", "")
ACTIVITIES_APP_PWD = os.environ.get("ACTIVITIES_APP_PASSWORD", "")

# Allowlist del Data Bot — solo gerencia. Si está vacío, todos del tenant.
DATA_ALLOWED_USERS = {
    e.strip().lower()
    for e in os.environ.get(
        "BOT_ALLOWED_USERS_DATA",
        "dsanchez@biodegradablesecuador.com,"
        "gsanchez@biodegradablesecuador.com,"
        "malvarado@biodegradablesecuador.com",
    ).split(",")
    if e.strip()
}

# Para el activities bot: cualquier colaborador del tenant.
# Si querés restringir, setear BOT_ALLOWED_USERS_ACTIVITIES en env.
ACTIVITIES_ALLOWED_USERS = {
    e.strip().lower()
    for e in os.environ.get("BOT_ALLOWED_USERS_ACTIVITIES", "").split(",")
    if e.strip()
}


def _build_adapter(app_id: str, app_pwd: str) -> BotFrameworkAdapter:
    kwargs: dict[str, Any] = {"app_id": app_id, "app_password": app_pwd}
    if APP_TENANT_ID:
        kwargs["channel_auth_tenant"] = APP_TENANT_ID
    return BotFrameworkAdapter(BotFrameworkAdapterSettings(**kwargs))


data_adapter = _build_adapter(DATA_APP_ID, DATA_APP_PWD)
activities_adapter = _build_adapter(ACTIVITIES_APP_ID, ACTIVITIES_APP_PWD)


async def _on_adapter_error(context: TurnContext, error: Exception) -> None:
    logger.exception("Adapter error: %s", error)
    try:
        await context.send_activity(
            "Lo siento, ocurrió un error procesando tu mensaje. "
            "Intenta de nuevo en un momento."
        )
    except Exception:
        pass


data_adapter.on_turn_error = _on_adapter_error
activities_adapter.on_turn_error = _on_adapter_error


# ===== Storage de conversation references =====
# Un solo archivo, dos secciones: {"data": {...}, "activities": {...}}
REFS_PATH = Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "conversation_refs.json"


# Fase 1: lock + escritura atómica + cuarentena via safe_json. Si este
# archivo se corrompía, el bot "olvidaba" a todos los usuarios (los check-ins
# y reminders proactivos dejaban de llegar) sin ningún error (auditoría H2).
_REFS_LOCK = safe_json.lock_for(REFS_PATH)


def _load_refs() -> dict[str, dict[str, dict[str, Any]]]:
    data = safe_json.load_json(REFS_PATH, lambda: {"data": {}, "activities": {}})
    # Migración del formato viejo (sin secciones)
    if "data" not in data and "activities" not in data:
        data = {"data": {}, "activities": data}
    data.setdefault("data", {})
    data.setdefault("activities", {})
    return data


def _save_refs(refs: dict[str, dict[str, dict[str, Any]]]) -> None:
    safe_json.save_json(REFS_PATH, refs)


def _save_ref_for_user(section: str, email: str, ref: ConversationReference) -> None:
    if not email:
        return
    with _REFS_LOCK:
        refs = _load_refs()
        refs.setdefault(section, {})[email.lower()] = ref.serialize()
        _save_refs(refs)
    logger.info("Saved %s ref for %s", section, email)


# ===== Helpers compartidos =====
FALLBACK_EMAIL = os.environ.get(
    "TRACKER_TARGET_USER", "malvarado@biodegradablesecuador.com"
).strip().lower()


_AAD_GENERIC_WORDS = {
    # Palabras vacías o genéricas del tenant — NO sirven para identificar
    "biodegradables", "ecuador", "del", "de", "la", "el", "y",
    "sa", "s.a.", "cia", "ltda",
}


def _normalize_words(text: str) -> set[str]:
    """Tokeniza y normaliza: lowercase + sin acentos + sin palabras genéricas."""
    import re
    import unicodedata
    if not text:
        return set()
    decomp = unicodedata.normalize("NFKD", text.strip().lower())
    no_accents = "".join(c for c in decomp if unicodedata.category(c) != "Mn")
    words = set(re.findall(r"[a-z0-9]+", no_accents))
    return words - _AAD_GENERIC_WORDS


def _match_name_to_collaborator(display_name: str) -> str | None:
    """Phase V (2026-06-11): match ROBUSTO por word-set, NO por substring.

    El alias debe ser SUBCONJUNTO ESTRICTO de las palabras del display name.
    Si "mateo alvarado" → 'malvarado@', requiere que tanto "mateo" como
    "alvarado" estén presentes. Solo "mateo" en una cadena tipo
    "José Mateo Solórzano" NO matchea.

    Prioriza alias con MÁS palabras (más específicos). Si hay empate por
    cantidad de palabras → devuelve None para forzar email aislado.
    """
    if not display_name:
        return None
    words = _normalize_words(display_name)
    if not words:
        return None
    try:
        from ask_agent import COLLABORATORS
    except Exception:
        return None

    best_email: str | None = None
    best_score = 0
    best_count = 0  # cuántos aliases distintos empataron con max score
    for alias, email in COLLABORATORS.items():
        alias_words = _normalize_words(alias)
        if not alias_words:
            continue
        # El alias debe estar 100% incluido en las words del display name
        if alias_words.issubset(words):
            score = len(alias_words)
            if score > best_score:
                best_score = score
                best_email = email
                best_count = 1
            elif score == best_score and email != best_email:
                # Empate con OTRO email — ambiguo, no podemos elegir
                best_count += 1
    if best_count > 1:
        return None  # Ambiguo
    return best_email


# Phase V (2026-06-11): Override absoluto AAD ID → email via env var.
# Formato: "aad_short:email,aad_short2:email2,..."
# Ejemplo: "435a855e:jsolorzano@biodegradablesecuador.com,7f2c1234:gsanchez@..."
# Esto gana sobre TODO. Útil cuando Teams pasa display name confuso o
# cuando un user nuevo recién entra y NO sabe el resolver.
_AAD_OVERRIDE_RAW = os.environ.get("AAD_ID_TO_EMAIL", "").strip()
AAD_OVERRIDE: dict[str, str] = {}
for _pair in _AAD_OVERRIDE_RAW.split(","):
    _pair = _pair.strip()
    if ":" in _pair:
        _k, _v = _pair.split(":", 1)
        _k = _k.strip().lower()
        _v = _v.strip().lower()
        if _k and "@" in _v:
            AAD_OVERRIDE[_k] = _v


# Phase V (2026-06-11): cache persistente AAD ID → email aprendido en runtime
# (cuando channel_data O props confiables resolvieron). Vive en
# `.claude-agent/aad_lookup.json`. Una vez que un AAD ID está mapeado a un
# email, queda fijo — no se reescribe ni desde display name match.
_AAD_LOOKUP_PATH = REFS_PATH.parent / "aad_lookup.json"


# Fase 1: el lookup AAD→email es el registro canónico de identidad. Si se
# corrompía y "se recuperaba" vacío, los usuarios volvían a resolverse por
# display name — reabriendo la contaminación entre usuarios (auditoría H2+H3).
_AAD_LOOKUP_LOCK = safe_json.lock_for(_AAD_LOOKUP_PATH)


def _load_aad_lookup() -> dict[str, str]:
    return safe_json.load_json(_AAD_LOOKUP_PATH, dict)


def _save_aad_lookup(lookup: dict[str, str]) -> None:
    try:
        safe_json.save_json(_AAD_LOOKUP_PATH, lookup)
    except Exception as e:
        logger.warning("aad_lookup save failed: %s", e)


def _remember_aad_email(aad_short: str, email: str, source: str) -> None:
    """Guarda persistente AAD short → email. Solo si NO había uno antes o
    si la fuente es de alta confianza (channel_data, props)."""
    if not aad_short or not email or "@" not in email:
        return
    if email.startswith("unidentified-"):
        return
    aad_short = aad_short.lower()
    email = email.lower()
    with _AAD_LOOKUP_LOCK:
        lookup = _load_aad_lookup()
        existing = lookup.get(aad_short)
        if existing == email:
            return  # ya estaba igual
        if existing and existing != email:
            # CONFLICTO. NO sobrescribimos automáticamente — loggeamos y
            # mantenemos el primero. Mateo puede forzar via env var o admin endpoint.
            logger.error(
                "AAD CONFLICT: aad_short=%s mapeado a %s, intentando %s (source=%s). "
                "MANTENIENDO %s. Para forzar: AAD_ID_TO_EMAIL env var o admin endpoint.",
                aad_short, existing, email, source, existing,
            )
            return
        lookup[aad_short] = email
        _save_aad_lookup(lookup)
    logger.info("AAD remembered: %s → %s (source=%s)", aad_short, email, source)


def _user_email(context: TurnContext) -> str:
    """Phase V (2026-06-11): resolver ROBUSTO. NUNCA mezcla users.

    Estrategia EN ORDEN (early-return en cada paso, gana el primero):
    0. **AAD_ID_TO_EMAIL env var** (override absoluto de Mateo).
    1. **aad_lookup.json** (cache aprendido — no se mueve una vez guardado).
    2. **channel_data.email / tenant.email** (alta confianza — Teams pasa).
    3. **additional_properties** (email / upn / userPrincipalName / mail).
    4. **Match EXACTO display name** word-set (sin substring).
    5. **Email aislado por AAD short id** — nunca cae a otro user.

    En cada resolución de alta confianza (2 y 3), persiste el aad→email
    en `aad_lookup.json` para que las próximas llamadas sean O(1).
    """
    aad_id_short = ""
    name = ""
    try:
        activity = context.activity
        from_prop = getattr(activity, "from_property", None)
        if from_prop:
            aad_id = (getattr(from_prop, "aad_object_id", "") or "").strip()
            aad_id_short = aad_id.split("-")[0].lower() if aad_id else ""
            name = (getattr(from_prop, "name", "") or "").strip()

            channel_data = getattr(activity, "channel_data", None) or {}
            props = getattr(from_prop, "additional_properties", None) or {}
            logger.info(
                "_user_email DEBUG: name='%s', aad_short='%s', "
                "channel_keys=%s, prop_keys=%s",
                name, aad_id_short,
                list(channel_data.keys()) if isinstance(channel_data, dict) else "n/a",
                list(props.keys()) if isinstance(props, dict) else "n/a",
            )

            # 0. ENV VAR OVERRIDE (Mateo manual)
            if aad_id_short and aad_id_short in AAD_OVERRIDE:
                email = AAD_OVERRIDE[aad_id_short]
                logger.info("Email via AAD_ID_TO_EMAIL override → %s", email)
                return email

            # 1. CACHE persistente
            if aad_id_short:
                lookup = _load_aad_lookup()
                if aad_id_short in lookup:
                    email = lookup[aad_id_short]
                    logger.info("Email via aad_lookup cache → %s", email)
                    return email

            # 2. channel_data (alta confianza)
            if isinstance(channel_data, dict):
                tenant = channel_data.get("tenant") or {}
                email = (
                    channel_data.get("email")
                    or tenant.get("email")
                    or ""
                )
                if email and "@" in email:
                    email = email.lower()
                    logger.info("Email via channel_data → %s", email)
                    _remember_aad_email(aad_id_short, email, "channel_data")
                    return email

            # 3. additional_properties (alta confianza)
            if isinstance(props, dict):
                for key in ("email", "upn", "userPrincipalName", "mail",
                            "preferredUsername"):
                    email = props.get(key) or ""
                    if email and "@" in email:
                        email = email.lower()
                        logger.info("Email via props.%s → %s", key, email)
                        _remember_aad_email(aad_id_short, email, f"props.{key}")
                        return email

            # Fase 2 (auditoría A1): el match por display name fue ELIMINADO
            # como fuente de identidad/autorización. Con alias de una palabra
            # ("gabriela"), una "Gabriela Bravo" del tenant resolvía a
            # gsanchez@ — sus marcas caían en el state ajeno Y su chat
            # sobrescribía el conversation ref de gsanchez@ (contaminación
            # bidireccional). Ahora el display name solo se loguea como PISTA
            # para que el admin registre el mapeo AAD→email.
            hint = _match_name_to_collaborator(name)
            logger.warning(
                "_user_email: NO RESUELTO display_name='%s' aad='%s'%s. "
                "Registrar con POST /admin/aad-lookup/set.",
                name, aad_id_short,
                f" (¿quizás {hint}? NO se asume)" if hint else "",
            )
    except Exception as e:
        logger.warning("_user_email: excepción: %s", e)

    # 5. Email aislado por AAD short id — SOLO si hay AAD id. Sin AAD id ya
    # no se cae al bucket compartido `unidentified-unknown@` (auditoría A4:
    # dos personas distintas compartían state, historial y conversation ref).
    if aad_id_short:
        isolated = f"unidentified-{aad_id_short}@biodegradablesecuador.com"
        logger.warning("Email aislado: %s (name='%s')", isolated, name)
        return isolated
    logger.error(
        "_user_email: turno RECHAZADO — sin AAD object id y sin fuentes "
        "confiables (name='%s'). No se crea state.", name,
    )
    return ""


def _is_allowed_data(email: str) -> bool:
    # Fase 2 (auditoría C1): FAIL-CLOSED. Antes, una env var vacía en un
    # redeploy le daba acceso a ventas/cartera/tools de gerencia a CUALQUIER
    # usuario del tenant.
    if not email:
        return False
    if not DATA_ALLOWED_USERS:
        logger.error(
            "BOT_ALLOWED_USERS_DATA está VACÍA — Data Bot fail-closed: "
            "nadie entra hasta configurarla."
        )
        return False
    return email.lower() in DATA_ALLOWED_USERS


def _is_allowed_activities(email: str) -> bool:
    if not email:
        return False
    if not ACTIVITIES_ALLOWED_USERS:
        return True  # cualquiera del tenant — intencional para este bot
    return email.lower() in ACTIVITIES_ALLOWED_USERS


_UNRESOLVED_MSG = (
    "🤔 No pude identificarte con certeza, así que no voy a registrar nada "
    "(esto protege que tus datos no caigan en el usuario equivocado).\n\n"
    "Pedile a Mateo que registre tu usuario — tu AAD id aparece en los "
    "logs del bot."
)


async def _resolve_or_reject(context: TurnContext) -> str:
    """Resuelve la identidad o rechaza el turno con un mensaje claro.

    Fase 2: un turno sin identidad confiable ya NO crea state, ni historial,
    ni conversation refs (auditoría A1/A4)."""
    email = _user_email(context)
    if not email:
        try:
            await context.send_activity(_UNRESOLVED_MSG)
        except Exception:
            logger.exception("No se pudo enviar el mensaje de identidad")
    return email


# ===== Data Bot =====
DATA_WELCOME = (
    "👋 ¡Hola! Soy el **Data Bot** de Biodegradables Ecuador.\n\n"
    "Te ayudo con consultas en tiempo real sobre la operación:\n"
    "• ¿Cuánto vendimos hoy / ayer / este mes?\n"
    "• ¿Cómo va el cumplimiento del mes vs la meta?\n"
    "• ¿Top clientes / Top vendedores?\n"
    "• ¿Quito vs Guayaquil?\n"
    "• ¿Leads, deals y pipeline de HubSpot?\n\n"
    "Tipea `/help` para ver detalle. Hablame natural — yo interpreto."
)

DATA_HELP = (
    "**Data Bot — comandos:**\n"
    "• Preguntá lo que quieras en lenguaje natural\n"
    "• `/help` — esta ayuda\n\n"
    "**Fuentes:** Contifico (ventas, en vivo) + HubSpot (CRM).\n\n"
    "Para tracker de tus actividades personales, usá el **Activities Bot**."
)


def _friendly_api_error(e: Exception) -> str:
    """Convierte excepciones de Claude API / rate limit / timeout en un mensaje
    amigable al usuario (sin traceback técnico)."""
    err_str = str(e).lower()
    if "credit balance" in err_str or "billing" in err_str or "purchase credits" in err_str:
        return (
            "⏸️ El asistente está temporalmente sin crédito en la API de Claude. "
            "Mateo ya está al tanto y lo está solucionando — apenas se resuelva, "
            "todo vuelve solo.\n\n"
            "Por mientras podés usar **`/checkin`** para abrir tu formulario del día "
            "(eso funciona sin IA)."
        )
    if "rate_limit" in err_str or "429" in err_str or "rate limit" in err_str:
        return (
            "⏳ El asistente está ocupado (rate limit). "
            "Esperá un minuto y volvé a preguntarme."
        )
    if "timeout" in err_str:
        return (
            "⏰ La respuesta demoró demasiado. Volvé a intentar — si vuelve a "
            "pasar, avisale a Mateo."
        )
    return (
        "❌ Algo no anda bien con el asistente ahora. Mateo está al tanto. "
        "Por mientras podés usar **`/checkin`** para tu formulario del día."
    )


# Saludos simples — los detectamos para responder fijo SIN llamar a Claude.
# Ahorra créditos y da mejor UX (la respuesta es instantánea).
_GREETING_PATTERNS: set[str] = {
    "hola", "holi", "holis", "holaa", "holaaa", "hi", "hello", "hey",
    "buenas", "saludos", "que tal", "qué tal",
    "buen dia", "buen día", "buenos dias", "buenos días",
    "buenas tardes", "buenas noches",
    "ola",  # typo común
}


def _is_greeting(text: str) -> bool:
    """True si el text es solo un saludo (sin pregunta concreta detrás)."""
    import string
    cleaned = text.lower().translate(
        str.maketrans("", "", string.punctuation)
    ).strip()
    return cleaned in _GREETING_PATTERNS


async def _on_turn_data(context: TurnContext) -> None:
    activity = context.activity

    if activity.type == ActivityTypes.conversation_update:
        if activity.members_added:
            for member in activity.members_added:
                if member.id != activity.recipient.id:
                    email = _user_email(context)
                    if email:
                        ref = TurnContext.get_conversation_reference(activity)
                        _save_ref_for_user("data", email, ref)
                    await context.send_activity(DATA_WELCOME)
        return

    if activity.type != ActivityTypes.message:
        return

    text = (activity.text or "").strip()
    if not text:
        return

    email = await _resolve_or_reject(context)
    if not email:
        return
    if not _is_allowed_data(email):
        logger.warning("Data Bot: usuario no autorizado: %s", email)
        await context.send_activity(
            "Lo siento, este bot es solo para gerencia. "
            "Si necesitás acceso, hablá con Daniel."
        )
        return

    ref = TurnContext.get_conversation_reference(activity)
    _save_ref_for_user("data", email, ref)

    if text.lower() in ("/help", "help", "?"):
        await context.send_activity(DATA_HELP)
        return
    if text.lower() in ("/clear", "/reset", "/nueva", "/nueva conversacion"):
        conversation_history.clear_history(email, "data")
        await context.send_activity(
            "🧹 Listo, arrancamos conversación nueva. Olvidé el contexto previo."
        )
        return

    # Saludos simples → respuesta fija (no toca Claude, ahorra créditos)
    if _is_greeting(text):
        await context.send_activity(
            "¡Hola! 👋 Soy el Data Bot. Preguntame sobre ventas, cobranzas, "
            "clientes o proyecciones. Ejemplos:\n"
            "• *cuánto vendimos hoy*\n"
            "• *top 5 deudores de Quito*\n"
            "• *proyección de ventas para fin de mes*\n\n"
            "Escribí **`/help`** para más detalle."
        )
        return

    # Cargar history multi-turn (last 12 turns, TTL 30 min)
    history = conversation_history.get_history(email, "data")

    # Para queries que pueden tardar > 15s (proyecciones + web search +
    # razonamiento), usar async-proactive: ack inmediato, procesar en
    # background, mandar el resultado via continue_conversation. Esto
    # esquiva el timeout de 15s de Bot Framework.
    is_long_query = any(
        kw in text.lower()
        for kw in [
            "proyect", "forecast", "escenario", "considerando",
            "qué pasa si", "que pasa si",
            "noticias", "actualidad", "situación actual", "situacion actual",
        ]
    )

    if is_long_query:
        await context.send_activity(
            "🔄 Procesando proyección — puede tardar 30-90 seg "
            "(histórico + web search + razonamiento). Te mando la respuesta "
            "en cuanto la tenga..."
        )
        # Guardar ref y procesar en background (fire-and-forget)
        ref = TurnContext.get_conversation_reference(activity)
        asyncio.create_task(_process_long_data_query(ref, text, email, history))
        return  # HTTP 200 vuelve YA a Bot Framework

    typing = Activity(type=ActivityTypes.typing)
    await context.send_activity(typing)

    try:
        answer = await asyncio.to_thread(
            ask_agent.ask, text, user_email=email, mode="data", history=history,
        )
    except Exception as e:
        logger.exception("Error en ask_agent (data): %s", e)
        await context.send_activity(_friendly_api_error(e))
        return

    if len(answer) > 25000:
        answer = answer[:25000] + "\n\n_(respuesta truncada)_"
    await context.send_activity(answer)
    # Persistir el turn (user + assistant)
    conversation_history.add_turns(email, "data", text, answer)


async def _process_long_data_query(
    ref: ConversationReference,
    text: str,
    email: str,
    history: list[dict] | None = None,
) -> None:
    """Procesa una query larga del Data Bot fuera del turn handler.

    Llamado via asyncio.create_task desde _on_turn_data. Cuando termina,
    usa `data_adapter.continue_conversation` para mandar el resultado como
    mensaje proactivo (esquivando el timeout de 15s del turn handler).
    """
    try:
        answer = await asyncio.to_thread(
            ask_agent.ask, text, user_email=email, mode="data", history=history,
        )
    except Exception as e:
        logger.exception("Long data query failed: %s", e)
        answer = _friendly_api_error(e)

    if len(answer) > 25000:
        answer = answer[:25000] + "\n\n_(respuesta truncada)_"

    async def cb(turn_context: TurnContext, msg: str = answer) -> None:
        await turn_context.send_activity(msg)

    try:
        await data_adapter.continue_conversation(ref, cb, bot_id=DATA_APP_ID)
        logger.info("Long query response enviada a %s", email)
        # Persistir el turn al history (solo si tuvo respuesta exitosa)
        conversation_history.add_turns(email, "data", text, answer)
    except Exception as e:
        logger.exception("Failed proactive send for long query: %s", e)


# ===== Activities Bot =====
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]

ACTIVITIES_WELCOME = (
    "👋 ¡Hola! Soy el **Activities Bot** — tu tracker personal.\n\n"
    "Cada colaborador tiene SUS propias actividades acá. Lo que marqués "
    "queda solo en tu sesión, y al cierre del día se manda un resumen a "
    "tu supervisor.\n\n"
    "**Automático:**\n"
    "• Lun-Vie 4:30 PM y Sáb 12:30 PM te llega un formulario con las "
    "actividades del día\n"
    "• Al completarlo, se manda el resumen al supervisor\n\n"
    "**Comandos:**\n"
    "• `/checkin` — abrir el formulario ahora\n"
    "• `/status` — ver tu progreso de la semana\n"
    "• `/help` — más detalle\n\n"
    "Si te falta una actividad o querés sumar algo extra, escribime "
    "natural (ej. _'agregame visita a Cliente X esta semana'_)."
)

ACTIVITIES_HELP = (
    "**Activities Bot — tu tracker personal:**\n"
    "• `/checkin` — formulario con tus actividades del día\n"
    "• `/status` — progreso de la semana actual\n"
    "• `/help` — esta ayuda\n\n"
    "**Schedule automático:** te escribo Lun-Vie 4:30 PM y Sáb 12:30 PM.\n\n"
    "Para consultas de ventas/cartera, usá el **Data Bot**."
)


def _build_checkin_card(user_email: str | None = None) -> Activity:
    """Construye una Adaptive Card con casillas/inputs para el check-in."""
    wk = activity_state.get_week(user_email)
    diarias = [
        (aid, a) for aid, a in wk["activities"].items() if a["tipo"] == "diaria"
    ]
    # Phase L: ordenar carry-overs primero, después por priority alta→media→baja
    from datetime import date as _date_cls2, timedelta as _td2
    _today_iso = activity_state._today().isoformat()  # TZ Ecuador (A9)
    _yest_iso = (activity_state._today() - _td2(days=1)).isoformat()
    diarias = activity_state.sort_activities_by_priority_then_carryover(
        diarias, _today_iso, _yest_iso
    )
    semanales = [
        (aid, a) for aid, a in wk["activities"].items() if a["tipo"] != "diaria"
    ]

    hoy = datetime.now(activity_state.LOCAL_TZ)
    fecha_str = f"{DIAS_ES[hoy.weekday()]} {hoy.day:02d}/{hoy.month:02d}"

    # Phase S+ (2026-06-09): cada sección envuelta en Container con
    # style="emphasis" para que se vean visualmente separadas como cuadros.
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "📋 Cierre del día",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": fecha_str.capitalize(),
            "spacing": "None",
            "isSubtle": True,
        },
    ]

    # ===== CONTAINER 1: Horario de hoy =====
    horario_items: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "⏰ Horario de hoy",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": "¿Trabajaste el horario estándar (8:30 AM – 5:30 PM)?",
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "Input.ChoiceSet",
            "id": "horario_estandar",
            "style": "expanded",
            "value": "si",
            "choices": [
                {"title": "✅ Sí, horario estándar", "value": "si"},
                {"title": "⏱️ No, falté o salí antes", "value": "no"},
            ],
        },
        # Sección que SOLO aplica si dijo NO. Adaptive Card 1.4 no soporta
        # condicional visual nativo, mostramos siempre con label "Solo si NO:"
        {
            "type": "TextBlock",
            "text": "📝 Si fue NO, completá lo siguiente:",
            "weight": "Bolder",
            "color": "Warning",
            "spacing": "Medium",
            "isSubtle": False,
        },
        {
            "type": "TextBlock",
            "text": "¿Notificaste con anticipación que ibas a faltar?",
            "wrap": True,
            "spacing": "Small",
            "isSubtle": True,
        },
        {
            "type": "Input.ChoiceSet",
            "id": "horario_notifico",
            "style": "expanded",
            "value": "no_aplica",
            "choices": [
                {"title": "📧 Sí, notifiqué por correo (medio formal)", "value": "si_correo"},
                {"title": "❌ No, no notifiqué", "value": "no_notifico"},
                {"title": "— (No aplica, trabajé normal)", "value": "no_aplica"},
            ],
        },
        {
            "type": "Input.Number",
            "id": "horario_horas_permiso",
            "label": "Horas de permiso / ausencia",
            "placeholder": "0",
            "min": 0,
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_franja",
            "label": "¿De qué hora a qué hora?",
            "placeholder": "Ej. 9:30 – 11:00",
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_motivo",
            "label": "Motivo de la ausencia / permiso",
            "placeholder": "Ej. reunión médica, emergencia familiar...",
            "isMultiline": True,
            "spacing": "Small",
        },
        {
            "type": "Input.Text",
            "id": "horario_porque_no_notifico",
            "label": "Si NO notificaste antes: ¿por qué?",
            "placeholder": "Ej. emergencia inesperada, sin batería...",
            "isMultiline": True,
            "spacing": "Small",
        },
    ]
    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": horario_items,
    })

    if not diarias and not semanales:
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": [{
                "type": "TextBlock",
                "text": (
                    "No tenés actividades configuradas todavía. "
                    "Para sumar una, simplemente escribime en este chat: "
                    "_'agregame visita a Cliente X'_, _'sumame revisar carteras semanales'_, "
                    "etc. — y las voy armando juntos a tu rutina."
                ),
                "wrap": True,
                "color": "Accent",
            }],
        })

    if diarias:
        diarias_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📅 Actividades diarias",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        for aid, a in diarias:
            meta = a.get("meta")
            unidad = a.get("unidad", "")
            meta_txt = f" (meta {meta} {unidad})" if meta else ""
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            is_co = activity_state.is_carryover_alta(a, _today_iso, _yest_iso)
            co_prefix = "⚠️ PENDIENTE DE AYER · " if is_co else ""
            color = "Attention" if is_co else "Default"
            es_cobranza = aid.startswith("cobranza-")
            diarias_items.append({
                "type": "TextBlock",
                "text": f"{co_prefix}{prio_badge}**{a['nombre']}**{meta_txt}",
                "wrap": True,
                "spacing": "Medium",
                "color": color,
            })
            if es_cobranza:
                diarias_items.append({
                    "type": "Input.ChoiceSet",
                    "id": f"estado__{aid}",
                    "style": "expanded",
                    "value": "no_contactado",
                    "choices": [
                        {"title": "📞 Contactado", "value": "contactado"},
                        {"title": "❌ No contactado", "value": "no_contactado"},
                    ],
                })
                diarias_items.append({
                    "type": "Input.Text",
                    "id": f"razon__{aid}",
                    "placeholder": (
                        "¿Qué te dijo el cliente? "
                        "(ej. 'paga el viernes', 'no contesta', 'pidió plazo de 15 días')"
                    ),
                    "isMultiline": True,
                })
            else:
                diarias_items.append({
                    "type": "Input.ChoiceSet",
                    "id": f"estado__{aid}",
                    "style": "expanded",
                    "value": "skip",
                    "choices": [
                        {"title": "✅ Hecho", "value": "hecho"},
                        {"title": "⚠️ Parcial", "value": "parcial"},
                        {"title": "❌ No hecho", "value": "no_hecho"},
                        {"title": "— Sin actividad / saltar", "value": "skip"},
                    ],
                })
                placeholder = (
                    f"Cuánto se hizo? (meta {meta})"
                    if meta is not None
                    else "Cuánto se hizo? (cantidad)"
                )
                diarias_items.append({
                    "type": "Input.Number",
                    "id": f"valor__{aid}",
                    "placeholder": placeholder,
                    "min": 0,
                })
                diarias_items.append({
                    "type": "Input.Text",
                    "id": f"razon__{aid}",
                    "placeholder": "Si Parcial o No hecho: por qué?",
                    "isMultiline": False,
                })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": diarias_items,
        })

    if semanales:
        semanales_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📌 Proyectos semanales",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        for aid, a in semanales:
            current = a.get("avance") or 0
            semanales_items.append({
                "type": "TextBlock",
                "text": f"**{a['nombre']}** — actual {current:.0f}%",
                "wrap": True,
                "spacing": "Medium",
            })
            semanales_items.append({
                "type": "Input.Number",
                "id": f"avance__{aid}",
                "placeholder": "Nuevo % avance (0-100). Vacío si no avanzaste.",
                "min": 0,
                "max": 100,
            })
            semanales_items.append({
                "type": "Input.Text",
                "id": f"notas__{aid}",
                "placeholder": "Notas breves (opcional)",
                "isMultiline": False,
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": semanales_items,
        })

    # Phase R (2026-06-08) — TikTok seguidores semanales (para users con
    # activity video-tiktok). Pregunta una vez por semana, principalmente lunes.
    has_tiktok = any(
        aid == "video-tiktok" for aid in wk["activities"].keys()
    )
    if has_tiktok:
        tt = activity_state.get_tiktok_seguidores_semana(user_email)
        tiktok_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📱 TikTok — seguidores",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        if tt:
            seguidores = tt.get("seguidores", 0)
            delta = tt.get("delta_vs_semana_anterior")
            if delta is not None:
                if delta > 0:
                    delta_str = f"📈 +{delta} vs semana anterior"
                    delta_color = "Good"
                elif delta < 0:
                    delta_str = f"📉 {delta} vs semana anterior"
                    delta_color = "Attention"
                else:
                    delta_str = "≈ igual que semana anterior"
                    delta_color = "Default"
                tiktok_items.append({
                    "type": "TextBlock",
                    "text": (
                        f"Esta semana arrancaste con **{seguidores}** seguidores · {delta_str}"
                    ),
                    "wrap": True,
                    "color": delta_color,
                    "spacing": "Small",
                })
            else:
                tiktok_items.append({
                    "type": "TextBlock",
                    "text": f"Esta semana arrancaste con **{seguidores}** seguidores.",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "Small",
                })
            tiktok_items.append({
                "type": "TextBlock",
                "text": "_(Si te equivocaste cargando, completá abajo para corregirlo.)_",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "None",
            })
            tiktok_items.append({
                "type": "Input.Number",
                "id": "tiktok_seguidores_inicio",
                "label": "Corregir seguidores de la semana (opcional)",
                "placeholder": str(seguidores),
                "min": 0,
            })
        else:
            tiktok_items.append({
                "type": "TextBlock",
                "text": (
                    "Es el inicio de la semana o todavía no cargaste tus seguidores. "
                    "¿Con cuántos seguidores arrancaste esta semana en TikTok?"
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            })
            tiktok_items.append({
                "type": "Input.Number",
                "id": "tiktok_seguidores_inicio",
                "label": "Seguidores al inicio de la semana",
                "placeholder": "0",
                "min": 0,
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": tiktok_items,
        })

    # Phase N — cierre de caja para info@/quito@
    user_email_l = (user_email or "").strip().lower()
    if user_email_l in CIERRE_CAJA_USERS:
        sucursal = SUCURSAL_POR_USER.get(user_email_l, "")
        fondo_sucursal = activity_state.get_fondo_caja(sucursal)
        cierre_items: list[dict[str, Any]] = [
            {
                "type": "TextBlock",
                "text": f"💵 Cierre de caja {sucursal}",
                "weight": "Bolder",
                "size": "ExtraLarge",
                "color": "Accent",
            },
            {
                "type": "TextBlock",
                "text": (
                    f"Contá las denominaciones del FONDO que dejás en caja "
                    f"(no las ventas — esas son aparte). "
                    f"El fondo de caja de {sucursal} debe ser **${fondo_sucursal:,.0f}**. "
                    "Yo verifico que las denominaciones sumen ese monto."
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            },
            {
                "type": "TextBlock",
                "text": "BILLETES (cantidad)",
                "weight": "Bolder",
                "size": "Small",
                "color": "Good",
                "spacing": "Medium",
            },
        ]
        for fid, label in [
            ("caja_b100", "$100"),
            ("caja_b50", "$50"),
            ("caja_b20", "$20"),
            ("caja_b10", "$10"),
            ("caja_b5", "$5"),
            ("caja_b1", "$1 (billete)"),
        ]:
            cierre_items.append({
                "type": "Input.Number",
                "id": fid,
                "label": label,
                "placeholder": "0",
                "min": 0,
            })
        cierre_items.append({
            "type": "TextBlock",
            "text": "MONEDAS (cantidad)",
            "weight": "Bolder",
            "size": "Small",
            "color": "Good",
            "spacing": "Medium",
        })
        for fid, label in [
            ("caja_m1", "$1 (moneda)"),
            ("caja_m050", "50¢"),
            ("caja_m025", "25¢"),
            ("caja_m010", "10¢"),
            ("caja_m005", "5¢"),
            ("caja_m001", "1¢"),
        ]:
            cierre_items.append({
                "type": "Input.Number",
                "id": fid,
                "label": label,
                "placeholder": "0",
                "min": 0,
            })
        cierre_items.append({
            "type": "Input.Text",
            "id": "caja_notas",
            "label": "Notas (opcional)",
            "placeholder": "Solo si hay algo a aclarar (ej. faltó moneda de 25¢)",
            "isMultiline": False,
            "spacing": "Medium",
        })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": cierre_items,
        })

        # ===== 🍫 Chocolates de reviews (Phase Q+R) =====
        choco = activity_state.get_chocolates_semana(user_email)
        chocolates_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "🍫 Chocolates (reviews Google / Facebook)",
            "weight": "Bolder",
            "size": "ExtraLarge",
            "color": "Accent",
        }]
        if not choco or not choco.get("stock_inicial"):
            chocolates_items.append({
                "type": "TextBlock",
                "text": (
                    "Es el inicio de la semana o todavía no cargaste tu stock. "
                    "¿Con cuántos chocolates arrancás esta semana?"
                ),
                "wrap": True,
                "isSubtle": True,
                "spacing": "Small",
            })
            chocolates_items.append({
                "type": "Input.Number",
                "id": "chocolates_inicial",
                "label": "Stock inicial de chocolates (no se podrá modificar después)",
                "placeholder": "0",
                "min": 0,
            })
        else:
            stock_actual = choco.get("stock_actual", 0)
            stock_inicial = choco.get("stock_inicial", 0)
            entregado = choco.get("total_entregado", 0)
            recargado = choco.get("total_recargado", 0)
            color_stock = (
                "Attention" if stock_actual <= activity_state.CHOCOLATES_UMBRAL
                else "Good"
            )
            stock_msg = (
                f"📦 **Stock actual: {stock_actual} chocolates**\n"
                f"_(inicial {stock_inicial} + recargas {recargado} − entregas {entregado})_"
            )
            chocolates_items.append({
                "type": "TextBlock",
                "text": stock_msg,
                "wrap": True,
                "color": color_stock,
                "weight": "Bolder",
                "spacing": "Small",
            })
            if stock_actual <= activity_state.CHOCOLATES_UMBRAL:
                chocolates_items.append({
                    "type": "TextBlock",
                    "text": (
                        "⚠️ **Quedan pocos chocolates.** "
                        "Solicitá más antes de quedarte sin."
                    ),
                    "wrap": True,
                    "color": "Attention",
                    "isSubtle": False,
                    "spacing": "Small",
                })
        chocolates_items.append({
            "type": "Input.Number",
            "id": "chocolates_recarga",
            "label": "Recarga / Restock recibido hoy (opcional)",
            "placeholder": "Solo si te dieron más chocolates hoy",
            "min": 0,
            "spacing": "Medium",
        })
        chocolates_items.append({
            "type": "Input.Number",
            "id": "chocolates_entregas",
            "label": "¿Cuántas entregas hiciste hoy? (= reviews recibidos)",
            "placeholder": "0",
            "min": 0,
            "spacing": "Small",
        })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": chocolates_items,
        })

    body.append({
        "type": "TextBlock",
        "text": "Al enviar, marco todo y mando el resumen a Daniel y Gabriela.",
        "isSubtle": True,
        "wrap": True,
        "spacing": "Large",
    })

    # Fase 2 (auditoría A5): el card embebe el contexto con el que fue
    # generado (usuario, fecha, semana). El submit los valida — un card
    # viejo que quedó vivo en el chat de Teams ya no escribe marcas en la
    # fecha/semana equivocada ni en otro usuario.
    _ctx = {
        "ctx_user": (user_email or "").strip().lower(),
        "ctx_fecha": activity_state._today().isoformat(),
        "ctx_wk": activity_state.week_key(),
    }
    actions: list[dict[str, Any]] = []
    if user_email_l in CIERRE_CAJA_USERS:
        actions.append({
            "type": "Action.Submit",
            "title": "🧮 Calcular total",
            "data": {"intent": "calc_cierre_caja", **_ctx},
        })
    actions.append({
        "type": "Action.Submit",
        "title": "💾 GUARDAR Y ENVIAR RESUMEN",
        "style": "positive",  # botón VERDE para que se resalte
        "data": {"intent": "submit_checkin", **_ctx},
    })

    card_json: dict[str, Any] = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }

    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])


async def _handle_checkin_submission(
    context: TurnContext, form_data: dict[str, Any], user_email: str
) -> None:
    # Fase 2 (auditoría A5): validar el contexto embebido en el card.
    # Los campos ctx_* faltan en cards generados antes de esta versión —
    # en ese caso se omite la validación (compat durante la migración).
    ctx_user = (form_data.get("ctx_user") or "").strip().lower()
    ctx_fecha = (form_data.get("ctx_fecha") or "").strip()
    hoy_iso = activity_state._today().isoformat()
    if ctx_user and ctx_user != (user_email or "").strip().lower():
        logger.warning(
            "submit_checkin RECHAZADO: card de %s enviado por %s",
            ctx_user, user_email,
        )
        await context.send_activity(
            "❌ Este formulario fue generado para otro usuario. "
            "Tipea `/checkin` para abrir el tuyo."
        )
        return
    if ctx_fecha and ctx_fecha != hoy_iso:
        logger.info(
            "submit_checkin tardío: card del %s enviado el %s por %s",
            ctx_fecha, hoy_iso, user_email,
        )
        await context.send_activity(
            f"⏰ Este formulario es del **{ctx_fecha}** y hoy es {hoy_iso} — "
            "no lo registré para evitar marcas en el día equivocado. "
            "Tipea `/checkin` para abrir el de hoy."
        )
        return

    marcadas_daily = 0
    marcadas_weekly = 0
    # Inicializar acá para que SIEMPRE exista (no solo si user en CIERRE_CAJA_USERS)
    choco_msg_extra = ""

    # Phase R (2026-06-08) — TikTok seguidores semanales
    tiktok_raw = form_data.get("tiktok_seguidores_inicio")
    if tiktok_raw not in (None, "", "0"):
        try:
            qty = int(float(tiktok_raw))
            if qty > 0:
                activity_state.set_tiktok_seguidores_semana(user_email, qty)
        except (TypeError, ValueError):
            pass

    # Phase K (rev R) — horario con notificación + permiso detallado
    today_iso = activity_state._today().isoformat()
    horario_estandar = (form_data.get("horario_estandar") or "si").strip().lower()
    if horario_estandar == "no":
        # Construir razón compuesta a partir de los campos nuevos
        notifico = (form_data.get("horario_notifico") or "no_aplica").strip()
        horas = (form_data.get("horario_horas_permiso") or "").strip()
        franja = (form_data.get("horario_franja") or "").strip()
        motivo = (form_data.get("horario_motivo") or "").strip()
        por_que_no = (form_data.get("horario_porque_no_notifico") or "").strip()

        notifico_label = {
            "si_correo": "✅ Sí notificó por correo (formal)",
            "no_notifico": "❌ NO notificó antes",
            "no_aplica": "(n/a)",
        }.get(notifico, notifico)

        partes = []
        if motivo:
            partes.append(f"Motivo: {motivo}")
        if horas:
            partes.append(f"{horas}h de permiso")
        if franja:
            partes.append(f"({franja})")
        partes.append(f"Notificación: {notifico_label}")
        if notifico == "no_notifico" and por_que_no:
            partes.append(f"No notificó porque: {por_que_no}")
        razon_compuesta = " · ".join(partes) or "Sin detalles"

        activity_state.set_day_schedule(
            user_email, today_iso,
            estandar=False,
            desde=franja.split("–")[0].strip() if "–" in franja else franja.split("-")[0].strip() if "-" in franja else "",
            hasta=franja.split("–")[1].strip() if "–" in franja else franja.split("-")[1].strip() if "-" in franja and len(franja.split("-")) > 1 else "",
            razon=razon_compuesta,
        )
    else:
        activity_state.set_day_schedule(
            user_email, today_iso, estandar=True
        )

    wk = activity_state.get_week(user_email)
    for aid, a in wk["activities"].items():
        if a["tipo"] == "diaria":
            estado = (form_data.get(f"estado__{aid}") or "skip").strip()
            valor_raw = form_data.get(f"valor__{aid}")
            razon = (form_data.get(f"razon__{aid}") or "").strip()
            es_cobranza = aid.startswith("cobranza-")

            if estado == "skip":
                continue

            try:
                if es_cobranza:
                    # Cobranzas: contactado / no_contactado → valor 1/0 con razón
                    if estado == "contactado":
                        activity_state.mark_daily(
                            aid, 1,
                            user_email=user_email,
                            notas=razon or "Contactado (sin detalle de la conversación)",
                        )
                    elif estado == "no_contactado":
                        activity_state.mark_daily(
                            aid, 0,
                            user_email=user_email,
                            notas=razon or "No contactado",
                        )
                    marcadas_daily += 1
                else:
                    # Activities regulares
                    if estado == "hecho":
                        valor = (
                            float(valor_raw)
                            if valor_raw not in (None, "", "0")
                            else float(a.get("meta") or 1)
                        )
                        activity_state.mark_daily(
                            aid, valor, user_email=user_email, notas=""
                        )
                    elif estado == "parcial":
                        valor = float(valor_raw) if valor_raw not in (None, "") else 0.0
                        activity_state.mark_daily(
                            aid, valor,
                            user_email=user_email,
                            notas=razon or "Parcial (sin razón especificada)",
                        )
                    elif estado == "no_hecho":
                        activity_state.mark_daily(
                            aid, 0,
                            user_email=user_email,
                            notas=razon or "No realizada (sin razón)",
                        )
                    marcadas_daily += 1
            except Exception as e:
                logger.warning("Error marcando %s para %s: %s", aid, user_email, e)
        else:
            avance_raw = form_data.get(f"avance__{aid}")
            notas = (form_data.get(f"notas__{aid}") or "").strip()
            if avance_raw in (None, ""):
                continue
            try:
                avance = float(avance_raw)
                activity_state.set_weekly_progress(
                    aid, avance, user_email=user_email, notas=notas or "",
                )
                marcadas_weekly += 1
            except (TypeError, ValueError):
                pass

    # Phase N — cierre de caja (solo info@/quito@). Procesamos ANTES de salir
    # con "no marcaste nada" porque puede ser que el user solo haya llenado el
    # cierre y no las actividades.
    cierre_email_result: dict | None = None
    user_email_l = (user_email or "").strip().lower()
    if user_email_l in CIERRE_CAJA_USERS:
        sucursal = SUCURSAL_POR_USER.get(user_email_l, "")
        denom_keys = (
            "caja_b100", "caja_b50", "caja_b20", "caja_b10", "caja_b5", "caja_b1",
            "caja_m1", "caja_m050", "caja_m025", "caja_m010", "caja_m005", "caja_m001",
        )
        # Construir denoms dict sin el prefijo "caja_"
        denoms: dict[str, int] = {}
        any_filled = False
        for k in denom_keys:
            raw = form_data.get(k)
            try:
                v = int(float(raw)) if raw not in (None, "") else 0
            except (TypeError, ValueError):
                v = 0
            denoms[k.replace("caja_", "")] = max(0, v)
            if v > 0:
                any_filled = True
        caja_notas = (form_data.get("caja_notas") or "").strip()

        # Solo guardar/mandar correo si llenó algo o agregó notas
        if any_filled or caja_notas:
            try:
                activity_state.set_cierre_caja(
                    user_email, today_iso, denoms,
                    notas=caja_notas, sucursal=sucursal, realizado=any_filled,
                )
                # Phase T (2026-06-09): NO mandamos correo separado de cierre.
                # El detalle del cierre + denominaciones ahora se incluye en
                # el correo consolidado del equipo a las 18:30 EC.
                cierre_email_result = {"ok": True, "to": [], "cc": []}
                # Phase P: card proactivo al validador (Daniel GYE / Gabriela UIO)
                try:
                    asyncio.create_task(
                        send_confirmacion_cierre_to_validador(
                            user_email, today_iso, sucursal, es_recordatorio=False,
                        )
                    )
                except Exception as e:
                    logger.exception("Error disparando card confirmación: %s", e)
            except Exception as e:
                logger.exception("Error guardando cierre de caja: %s", e)

        # Phase Q+R (2026-06-05): chocolates de reviews
        try:
            stock_inicial_raw = form_data.get("chocolates_inicial")
            entregas_raw = form_data.get("chocolates_entregas")
            recarga_raw = form_data.get("chocolates_recarga")
            # Set stock inicial si vino (Phase R: solo si todavía no existe)
            if stock_inicial_raw not in (None, "", "0"):
                try:
                    qty = int(float(stock_inicial_raw))
                    if qty > 0:
                        activity_state.set_chocolates_stock_inicial(user_email, qty)
                except (TypeError, ValueError):
                    pass
            # Phase R: recarga del día
            if recarga_raw not in (None, "", "0"):
                try:
                    qty = int(float(recarga_raw))
                    if qty > 0:
                        activity_state.add_chocolates_recarga(
                            user_email, today_iso, qty,
                        )
                except (TypeError, ValueError):
                    pass
            # Agregar entregas de hoy si vino
            if entregas_raw not in (None, ""):
                try:
                    qty = int(float(entregas_raw))
                    if qty > 0:
                        activity_state.add_chocolates_entrega(
                            user_email, today_iso, qty,
                        )
                except (TypeError, ValueError):
                    pass
            # Chequear stock actual + alerta si <= umbral
            choco = activity_state.get_chocolates_semana(user_email)
            if choco:
                stock_actual = choco.get("stock_actual", 0)
                if stock_actual <= activity_state.CHOCOLATES_UMBRAL:
                    if not choco.get("alerta_5_enviada"):
                        choco_msg_extra = (
                            f"\n\n🍫⚠️ **Atención: quedan {stock_actual} chocolates.** "
                            f"Solicitá más esta semana así no te quedas sin para "
                            f"los clientes que dejen review."
                        )
                        activity_state.marcar_alerta_chocolates_enviada(user_email)
                    else:
                        choco_msg_extra = (
                            f"\n\n🍫 Stock actual: **{stock_actual} chocolates** "
                            f"(ya te avisé esta semana de solicitar más)."
                        )
                else:
                    choco_msg_extra = (
                        f"\n\n🍫 Stock actual: **{stock_actual} chocolates**."
                    )
        except Exception as e:
            logger.exception("Error procesando chocolates: %s", e)

    if marcadas_daily == 0 and marcadas_weekly == 0 and not cierre_email_result:
        await context.send_activity(
            "👀 No marcaste nada en el formulario. Tipea `/checkin` para volver a abrirlo."
        )
        return

    # Phase O (2026-06-02): NO mandamos email individual cada vez que alguien marca.
    # A las 18:30 EC un job consolidado manda UN solo correo a Daniel+Gabriela con
    # todos los colaboradores. Solo confirmamos en chat. El cierre de caja SÍ se
    # manda al instante porque es info financiera urgente.
    try:
        if marcadas_daily > 0 or marcadas_weekly > 0:
            extra_msg = ""
            if cierre_email_result and cierre_email_result.get("ok"):
                cc_str = ", ".join(cierre_email_result.get("cc", []))
                extra_msg = (
                    f"\n\n💵 Cierre de caja enviado a `{', '.join(cierre_email_result.get('to', []))}`"
                    + (f" (CC: {cc_str})" if cc_str else "")
                    + "."
                )
            await context.send_activity(
                f"✅ Marcadas **{marcadas_daily} diarias** + **{marcadas_weekly} semanales**.\n\n"
                f"📧 El resumen consolidado del equipo llega a Daniel y Gabriela "
                f"hoy 6:30 PM."
                + extra_msg
                + choco_msg_extra
            )
        elif cierre_email_result and cierre_email_result.get("ok"):
            cc_str = ", ".join(cierre_email_result.get("cc", []))
            await context.send_activity(
                f"💵 Cierre de caja registrado y enviado a "
                f"`{', '.join(cierre_email_result.get('to', []))}`"
                + (f" (CC: {cc_str})" if cc_str else "")
                + "."
                + choco_msg_extra
            )
    except Exception as e:
        logger.exception("Error confirmando check-in para %s: %s", user_email, e)
        await context.send_activity(
            f"✅ Marqué tus actividades, **pero algo falló al confirmar:**\n```\n{e}\n```"
        )


async def _on_turn_activities(context: TurnContext) -> None:
    activity = context.activity

    if activity.type == ActivityTypes.conversation_update:
        if activity.members_added:
            for member in activity.members_added:
                if member.id != activity.recipient.id:
                    email = _user_email(context)
                    if email:
                        ref = TurnContext.get_conversation_reference(activity)
                        _save_ref_for_user("activities", email, ref)
                    await context.send_activity(ACTIVITIES_WELCOME)
        return

    if activity.type != ActivityTypes.message:
        return

    # Submission de Adaptive Card
    if activity.value and isinstance(activity.value, dict):
        intent = activity.value.get("intent")
        if intent == "submit_checkin":
            email = await _resolve_or_reject(context)
            if not email:
                return
            if not _is_allowed_activities(email):
                await context.send_activity("No tenés acceso a este bot.")
                return
            ref = TurnContext.get_conversation_reference(activity)
            _save_ref_for_user("activities", email, ref)
            await _handle_checkin_submission(context, activity.value, email)
            return
        # Phase U: handlers del card de ruta de José
        if isinstance(intent, str) and intent.startswith("jose_"):
            email = await _resolve_or_reject(context)
            if not email:
                return
            if not _is_allowed_activities(email):
                await context.send_activity("No tenés acceso a este bot.")
                return
            ref = TurnContext.get_conversation_reference(activity)
            _save_ref_for_user("activities", email, ref)
            await _handle_jose_intent(context, intent, activity.value, JOSE_EMAIL)
            return
        if intent in ("calc_apertura_caja", "submit_apertura_caja"):
            email = await _resolve_or_reject(context)
            if not email:
                return
            if not _is_allowed_activities(email):
                await context.send_activity("No tenés acceso a este bot.")
                return
            ref = TurnContext.get_conversation_reference(activity)
            _save_ref_for_user("activities", email, ref)

            denoms_keys = (
                "apertura_b100","apertura_b50","apertura_b20","apertura_b10","apertura_b5","apertura_b1",
                "apertura_m1","apertura_m050","apertura_m025","apertura_m010","apertura_m005","apertura_m001",
            )
            denoms: dict[str, int] = {}
            for k in denoms_keys:
                raw = activity.value.get(k)
                try:
                    v = int(float(raw)) if raw not in (None, "") else 0
                except (TypeError, ValueError):
                    v = 0
                denoms[k.replace("apertura_", "")] = max(0, v)
            sucursal = SUCURSAL_POR_USER.get(email, "")
            notas = (activity.value.get("apertura_notas") or "").strip()
            calc = activity_state.calcular_cierre_caja(denoms, sucursal=sucursal)

            if intent == "calc_apertura_caja":
                detalle = ""
                for d in calc["detalle_billetes"] + calc["detalle_monedas"]:
                    if d["cantidad"] > 0:
                        detalle += f"\n• {d['label']} × {d['cantidad']} = ${d['subtotal']:,.2f}"
                if not detalle:
                    detalle = "\n_(todos los campos en 0)_"
                msg = (
                    f"🧮 **Verificación del fondo de apertura**\n"
                    f"{detalle}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 **TOTAL CONTADO:** ${calc['total']:,.2f}\n"
                    f"🎯 Fondo esperado: ${calc['fondo_esperado']:,.2f}\n"
                    f"{calc['status_label']}\n\n"
                    f"_Si está bien, dale **💾 Guardar apertura**._"
                )
                await context.send_activity(msg)
                return

            # submit_apertura_caja → guardar + email
            today_iso = activity_state._today().isoformat()
            any_filled = any(v > 0 for v in denoms.values())
            if not any_filled and not notas:
                await context.send_activity(
                    "👀 No marcaste ninguna denominación. Si arrancaste con caja en 0, "
                    "explicalo en Notas."
                )
                return
            try:
                activity_state.set_apertura_caja(
                    email, today_iso, denoms,
                    notas=notas, sucursal=sucursal,
                )
                # Email a Daniel + Gabriela Sánchez CC Mateo
                try:
                    from ask_agent import _send_apertura_caja_email
                    result = await asyncio.to_thread(
                        _send_apertura_caja_email,
                        email, today_iso, sucursal,
                    )
                    cc_str = ", ".join(result.get("cc", []))
                    await context.send_activity(
                        f"✅ Apertura registrada · {calc['status_label']}\n\n"
                        f"📧 Email enviado a `{', '.join(result.get('to', []))}`"
                        + (f" (CC: {cc_str})" if cc_str else "")
                    )
                except Exception as e:
                    logger.exception("Error mandando email apertura: %s", e)
                    await context.send_activity(
                        f"✅ Apertura registrada · {calc['status_label']}\n\n"
                        f"⚠️ Falló envío del correo: `{e}`"
                    )
            except Exception as e:
                logger.exception("Error guardando apertura: %s", e)
                await context.send_activity(f"❌ Error al guardar: `{e}`")
            return

        if intent == "confirmar_cierre":
            # El validador (Daniel/Gabriela Sánchez) confirma la recepción del efectivo
            email = await _resolve_or_reject(context)
            if not email:
                return
            ref = TurnContext.get_conversation_reference(activity)
            _save_ref_for_user("activities", email, ref)

            emisor = activity.value.get("emisor_email", "").strip().lower()
            fecha = activity.value.get("fecha", "").strip()
            sucursal = activity.value.get("sucursal", "")
            entregado = float(activity.value.get("entregado") or 0)
            estado = (activity.value.get("confirm_estado") or "confirmado").strip()
            monto_raw = activity.value.get("confirm_monto")
            razon = (activity.value.get("confirm_razon") or "").strip()

            if not emisor or not fecha:
                await context.send_activity(
                    "❌ Datos incompletos del card de confirmación."
                )
                return

            # Fase 2 (auditoría A11): solo el validador designado de la
            # sucursal o un supervisor pueden confirmar — el payload del
            # card es manipulable y antes cualquier usuario del tenant
            # podía escribir confirmaciones en cierres ajenos.
            autorizados = set(VALIDADOR_CIERRE_POR_CIUDAD.values()) | SUPERVISORS_ONLY
            if email not in autorizados:
                logger.warning(
                    "confirmar_cierre RECHAZADO: %s no es validador (emisor=%s)",
                    email, emisor,
                )
                await context.send_activity(
                    "❌ Solo el validador designado puede confirmar cierres de caja."
                )
                return

            try:
                monto_recibido = (
                    float(monto_raw) if monto_raw not in (None, "") else None
                )
            except (TypeError, ValueError):
                monto_recibido = None

            try:
                activity_state.set_cierre_caja_confirmacion(
                    emisor, fecha,
                    estado=estado, validador=email,
                    monto_recibido=monto_recibido, razon=razon,
                )
            except Exception as e:
                logger.exception("Error guardando confirmación cierre: %s", e)
                await context.send_activity(
                    f"❌ Algo falló guardando la confirmación: `{e}`"
                )
                return

            # Mandar email de resultado al equipo
            try:
                from ask_agent import _send_confirmacion_cierre_email
                await asyncio.to_thread(
                    _send_confirmacion_cierre_email,
                    emisor, fecha, sucursal, email,
                    estado, monto_recibido, razon, entregado,
                )
            except Exception as e:
                logger.exception("Error mandando email confirmación: %s", e)

            # Respuesta al validador en Teams
            if estado == "confirmado":
                msg = (
                    f"✅ Listo, recepción confirmada por **${entregado:,.2f}**. "
                    f"El equipo recibe el correo con la confirmación."
                )
            elif estado == "discrepancia":
                diff = (monto_recibido or 0) - entregado
                sign = "+" if diff > 0 else ""
                msg = (
                    f"⚠️ Discrepancia registrada. Reportado: **${entregado:,.2f}** "
                    f"vs Recibido: **${monto_recibido:,.2f}** "
                    f"({sign}${diff:,.2f}). El equipo recibe el correo con el detalle."
                )
            else:
                msg = (
                    "📝 Marcado como pendiente de recibir. Cuando lo tengas en "
                    "mano, abrí este chat y volvé a confirmar."
                )
            await context.send_activity(msg)
            return

        if intent == "calc_cierre_caja":
            # Calcula totales del cierre EN VIVO sin guardar. El usuario puede
            # ajustar valores y volver a calcular antes de "Guardar y enviar".
            denoms_keys = (
                "caja_b100", "caja_b50", "caja_b20", "caja_b10", "caja_b5", "caja_b1",
                "caja_m1", "caja_m050", "caja_m025", "caja_m010", "caja_m005", "caja_m001",
            )
            denoms: dict[str, int] = {}
            for k in denoms_keys:
                raw = activity.value.get(k)
                try:
                    v = int(float(raw)) if raw not in (None, "") else 0
                except (TypeError, ValueError):
                    v = 0
                denoms[k.replace("caja_", "")] = max(0, v)
            # Phase R: usar fondo correcto según sucursal del usuario
            sucursal_user = SUCURSAL_POR_USER.get(
                _user_email(context) or "", None
            )
            calc = activity_state.calcular_cierre_caja(denoms, sucursal=sucursal_user)
            # Desglose denominación por denominación para que vean qué sumó
            detalle = ""
            for d in calc["detalle_billetes"]:
                if d["cantidad"] > 0:
                    detalle += (
                        f"\n• {d['label']} × {d['cantidad']} = "
                        f"${d['subtotal']:,.2f}"
                    )
            for d in calc["detalle_monedas"]:
                if d["cantidad"] > 0:
                    detalle += (
                        f"\n• {d['label']} × {d['cantidad']} = "
                        f"${d['subtotal']:,.2f}"
                    )
            if not detalle:
                detalle = "\n_(todos los campos en 0 — no llenaste nada todavía)_"

            # Phase S: nuevo formato — comparar contado vs fondo esperado
            msg = (
                f"🧮 **Cálculo del fondo en caja**\n"
                f"{detalle}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 **Total billetes:** ${calc['total_billetes']:,.2f}\n"
                f"🪙 **Total monedas:** ${calc['total_monedas']:,.2f}\n"
                f"💰 **TOTAL CONTADO:** ${calc['total']:,.2f}\n\n"
                f"🎯 Fondo esperado: ${calc['fondo_esperado']:,.2f}\n"
                f"{calc['status_label']}\n\n"
                f"_Si está bien, dale a **💾 Guardar y enviar resumen**. "
                f"Si querés ajustar, cambiá los valores y volvé a tocar **🧮 Calcular total**._"
            )
            await context.send_activity(msg)
            return

    text = (activity.text or "").strip()
    if not text:
        return

    email = await _resolve_or_reject(context)
    if not email:
        return
    if not _is_allowed_activities(email):
        logger.warning("Activities Bot: no autorizado: %s", email)
        await context.send_activity("No tenés acceso a este bot.")
        return

    ref = TurnContext.get_conversation_reference(activity)
    _save_ref_for_user("activities", email, ref)

    lower = text.lower()

    # Phase U (2026-06-09): José Solórzano — CUALQUIER mensaje que escriba
    # dispara el card de ruta actualizado con la última lista de Contifico.
    # No le mandamos check-in normal de actividades.
    if email == JOSE_EMAIL:
        await context.send_activity(
            "🚚 Acá tenés tu ruta actualizada al momento:"
        )
        await context.send_activity(_build_jose_ruta_card(JOSE_EMAIL))
        return

    # Fase 2 (auditoría H17): los supervisores no trackean actividades
    # propias — no se les genera card ni state (el saludo de Daniel le
    # creaba un bucket vacío que después había que limpiar con wipe).
    # Conservan el chat natural (tools de supervisor en modo activities).
    es_supervisor = email in SUPERVISORS_ONLY
    _SUPERVISOR_CARD_MSG = (
        "👀 Sos supervisor — no trackeás actividades propias, así que no te "
        "abro formulario. Preguntame por las actividades del equipo, o usá "
        "el Data Bot para ventas."
    )

    if lower in ("/help", "help", "?"):
        await context.send_activity(ACTIVITIES_HELP)
        return
    if lower in ("/checkin", "/check-in", "/test-checkin"):
        if es_supervisor:
            await context.send_activity(_SUPERVISOR_CARD_MSG)
            return
        await context.send_activity(_build_checkin_card(email))
        return

    # Saludos simples → respuesta fija + abrir el card. NO toca Claude (gratis).
    if _is_greeting(text):
        if es_supervisor:
            await context.send_activity(_SUPERVISOR_CARD_MSG)
            return
        await context.send_activity(
            "¡Hola! 👋 Te dejo el formulario del día para marcar tus actividades:"
        )
        await context.send_activity(_build_checkin_card(email))
        return

    # Natural language que el user usa cuando quiere ABRIR EL FORMULARIO
    # (no marcar UNA actividad puntual). Pasamos directo a la card en vez
    # de mandarlo a Claude que respondería con texto.
    card_keywords = [
        "marcar mis actividades", "marcar actividades", "marcar el dia",
        "marcar el día", "marcar todo", "marcar mi dia", "marcar mi día",
        "abrir el formulario", "abrir formulario",
        "quiero marcar mis", "quiero hacer el check",
        "ver actividades del", "mostrar el formulario",
        "checkin del dia", "checkin del día",
        "cerrar el dia", "cerrar el día", "cierre del dia", "cierre del día",
        "marcar", "checkin",  # short forms — last so longer ones match first
    ]
    if any(kw in lower for kw in card_keywords):
        if es_supervisor:
            await context.send_activity(_SUPERVISOR_CARD_MSG)
            return
        await context.send_activity(_build_checkin_card(email))
        return
    if lower in ("/status", "/week"):
        if es_supervisor:
            await context.send_activity(_SUPERVISOR_CARD_MSG)
            return
        wk = activity_state.get_week(email)
        monday, friday = activity_state.week_range(activity_state.week_key())
        summary = f"📊 **Tu semana {activity_state.week_key()}** ({monday}–{friday})\n\n"
        for aid, a in wk["activities"].items():
            if a["tipo"] == "diaria":
                total = activity_state.daily_total(a)
                meta = a.get("meta") or 0
                summary += f"• {a['nombre']}: {total:.0f}"
                if meta:
                    cumpl = activity_state.daily_compliance(a) or 0
                    summary += f" / {meta*5:.0f} ({cumpl*100:.0f}%)"
                summary += "\n"
            else:
                summary += f"• {a['nombre']}: {a.get('avance', 0):.0f}%"
                if a.get("notas"):
                    summary += f" — {a['notas']}"
                summary += "\n"
        await context.send_activity(summary)
        return

    # Comando /clear para resetear conversación
    if lower in ("/clear", "/reset", "/nueva", "/nueva conversacion"):
        conversation_history.clear_history(email, "activities")
        await context.send_activity(
            "🧹 Listo, arrancamos conversación nueva. Olvidé el contexto previo."
        )
        return

    # Texto natural — delegamos a ask_agent en modo activities
    # con history multi-turn (last 12 turns, TTL 30 min)
    history = conversation_history.get_history(email, "activities")

    typing = Activity(type=ActivityTypes.typing)
    await context.send_activity(typing)
    try:
        answer = await asyncio.to_thread(
            ask_agent.ask, text, user_email=email, mode="activities", history=history,
        )
    except Exception as e:
        logger.exception("Error en ask_agent (activities): %s", e)
        await context.send_activity(_friendly_api_error(e))
        return

    if len(answer) > 25000:
        answer = answer[:25000] + "\n\n_(respuesta truncada)_"
    await context.send_activity(answer)
    # Persistir el turn al history
    conversation_history.add_turns(email, "activities", text, answer)


# ===== Daily news brief (Phase I) =====
async def generate_daily_news_brief() -> None:
    """Job nocturno: corre web_search via Claude para preparar el contexto
    actual que el Data Bot va a usar en las queries del día.

    Corre 6 AM EC para que esté listo antes de daily_report (8 AM) y antes
    que Daniel/Gabriela hagan queries de proyección.
    """
    try:
        logger.info("generate_daily_news_brief: arrancando")
        # Correr en thread porque la API call es sync
        await asyncio.to_thread(news_brief.generate_brief)
        logger.info("generate_daily_news_brief: brief generado OK")
    except Exception as e:
        logger.exception("generate_daily_news_brief falló: %s", e)


# ===== Weekly summaries (Phase G.2) =====
async def send_weekly_summaries() -> None:
    """Envía el resumen semanal de cada colaborador a sus supervisores.

    Corre viernes 17:00 EC. Itera por todos los usuarios con state, llama
    _send_weekly_summary_email, envía via graph_mail al supervisor configurado.
    """
    users = activity_state.list_known_users()
    if not users:
        logger.info("send_weekly_summaries: no hay users con state")
        return

    enviados = 0
    errores = 0
    for email in users:
        # Pseudo-users `unidentified-*` no tienen supervisor ni son reales
        if email.lower().startswith("unidentified-"):
            continue
        try:
            result = _send_weekly_summary_email(user_email=email)
            logger.info("Weekly summary enviado para %s → %s",
                        email, result.get("to"))
            enviados += 1
        except Exception as e:
            logger.exception("Falló weekly summary para %s: %s", email, e)
            errores += 1
    logger.info("send_weekly_summaries: %d enviados, %d errores", enviados, errores)


# ===== Auto-asignación de cobranzas (Phase F) =====
# Mapeo ciudad → colaborador responsable de cobranza en esa plaza
COBRANZA_COLABORADORES = {
    "UIO": "quito@biodegradablesecuador.com",
    "GYE": "info@biodegradablesecuador.com",
}


def _slugify(text: str, maxlen: int = 40) -> str:
    """'CLIENTE Ñ SA' → 'cliente-n-sa'. Para activity_id en kebab-case."""
    s = _unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    s = _re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:maxlen] or "cliente"


async def auto_assign_cobranzas() -> None:
    """Cada mañana asigna las cobranzas vencidas top de cada ciudad al
    colaborador correspondiente. Los items aparecen en el check-in card
    del colaborador esa misma tarde.

    Idempotente: si una cobranza para el mismo cliente ya se asignó hoy,
    no la duplica.
    """
    today_str = activity_state._today().isoformat()  # TZ Ecuador (A9)
    asignadas = 0
    errores = 0

    for ciudad, target_user in COBRANZA_COLABORADORES.items():
        try:
            top = contifico_client.cartera_vencida_por_ciudad(ciudad, n=5)
        except Exception as e:
            logger.exception("Cobranza pull falló para %s: %s", ciudad, e)
            errores += 1
            continue

        if not top:
            logger.info("Cobranza %s: sin clientes vencidos hoy", ciudad)
            continue

        for c in top:
            cliente_slug = _slugify(c["cliente"])
            activity_id = f"cobranza-{cliente_slug}-{today_str}"
            nombre = (
                f"📞 Cobranza: {c['cliente']} — "
                f"${c['saldo_vencido']:,.0f} "
                f"({c['dias_atraso_max']}d atraso)"
            )
            try:
                activity_state.add_adhoc(
                    activity_id, nombre,
                    user_email=target_user,
                    tipo="unica",
                    meta=1,
                    unidad="cliente contactado",
                )
                asignadas += 1
            except ValueError:
                # Ya existe — idempotente, skip
                pass
            except Exception as e:
                logger.exception(
                    "Error asignando cobranza %s a %s: %s",
                    activity_id, target_user, e,
                )
                errores += 1

    logger.info(
        "auto_assign_cobranzas: %d asignadas, %d errores", asignadas, errores
    )


# ===== Reminders proactivos (de gerencia a colaboradores) =====
async def deliver_due_reminders() -> None:
    """Encuentra reminders vencidos y los entrega via activities_adapter."""
    due = reminders.get_due_reminders()
    if not due:
        return

    refs = _load_refs().get("activities", {})
    logger.info("deliver_due_reminders: %d vencidos, %d activities refs",
                len(due), len(refs))

    for r in due:
        target = r["target_user"]
        ref_dict = refs.get(target)
        if not ref_dict:
            logger.warning(
                "Reminder %s para %s: no hay activities ref (todavia no hablo al bot)",
                r["id"], target,
            )
            continue

        # Etiqueta del autor: "Daniel" si el created_by es dsanchez, etc.
        creator = r.get("created_by", "")
        creator_label = "gerencia"
        if "dsanchez" in creator:
            creator_label = "Daniel"
        elif "gsanchez" in creator:
            creator_label = "Gabriela"

        msg = (
            f"🔔 **Recordatorio de {creator_label}**:\n\n"
            f"{r['message']}"
        )

        try:
            ref = ConversationReference().deserialize(ref_dict)

            async def cb(turn_context: TurnContext, m: str = msg) -> None:
                await turn_context.send_activity(m)

            await activities_adapter.continue_conversation(
                ref, cb, bot_id=ACTIVITIES_APP_ID
            )
            reminders.mark_sent(r["id"])
            logger.info("Reminder %s entregado a %s", r["id"], target)

            # Reprogramar si es recurrente (Phase G.1)
            next_rec = reminders.reschedule_recurring(r)
            if next_rec:
                logger.info(
                    "Reminder %s recurrente '%s' reprogramado a %s (nuevo id %s)",
                    r["id"], r.get("recurrence"), next_rec["send_at"], next_rec["id"],
                )
        except Exception as e:
            logger.exception("Falló reminder %s a %s: %s", r["id"], target, e)


# ===== Proactive messaging (check-in del activities bot) =====
# Emails que tienen horarios CUSTOM y NO entran en el send_daily_checkin general
INFO_EMAIL = "info@biodegradablesecuador.com"
QUITO_EMAIL = "quito@biodegradablesecuador.com"
JOSE_EMAIL = "jsolorzano@biodegradablesecuador.com"  # Phase U — chofer GYE
# Horarios/destinatarios del check-in viven en core_config (CHECKIN_*).

# Phase N (2026-06-02): cierre de caja diario en sub-card del check-in
CIERRE_CAJA_USERS = {INFO_EMAIL, QUITO_EMAIL}
SUCURSAL_POR_USER = {
    INFO_EMAIL: "Guayaquil",
    QUITO_EMAIL: "Quito",
    JOSE_EMAIL: "Guayaquil",
}

# Phase U (2026-06-09): usuarios que reciben el card de ruta de envíos
# (no el check-in normal de actividades). Por ahora solo José en GYE.
ROUTE_USERS: set[str] = {JOSE_EMAIL}

# Phase P (2026-06-05): validador del efectivo entregado en cada sucursal.
# Cuando info@/quito@ guarda su cierre, el validador correspondiente recibe
# un card proactivo para confirmar que recibió el monto reportado.
VALIDADOR_CIERRE_POR_CIUDAD = {
    "Guayaquil": "dsanchez@biodegradablesecuador.com",  # Daniel recibe GYE
    "Quito": "gsanchez@biodegradablesecuador.com",      # Gabriela Sánchez recibe UIO
}

# Supervisores que NO trackean actividades propias — solo reciben los reportes
# de los colaboradores. Se excluyen del send_daily_checkin y NO se les crean
# actividades aunque haya un ref del bot por accidente.
SUPERVISORS_ONLY: set[str] = {
    "dsanchez@biodegradablesecuador.com",  # Daniel — gerente general
}

# Phase U: el resumen del día de José va solo a Daniel + Mateo
# (Gabriela maneja UIO, José es chofer GYE)
JOSE_SUMMARY_TO = [
    "dsanchez@biodegradablesecuador.com",
    "malvarado@biodegradablesecuador.com",
]


async def send_daily_checkin(
    exclude: set[str] | None = None,
    only: set[str] | None = None,
) -> None:
    """Envía la Adaptive Card de check-in a colaboradores registrados.

    Args:
        exclude: emails a excluir (ej. info@ que tiene su propio horario)
        only: si se pasa, solo envía a estos emails. Útil para el job del
              17:15 que solo dispara para info@.
    """
    refs = _load_refs()
    activities_refs = refs.get("activities", {})
    if not activities_refs:
        logger.warning("send_daily_checkin: no hay refs del activities bot")
        return

    exclude = exclude or set()
    for email, ref_dict in activities_refs.items():
        email_l = email.lower()
        if only is not None and email_l not in only:
            continue
        if email_l in exclude:
            continue
        # Supervisores nunca reciben el check-in card
        if email_l in SUPERVISORS_ONLY:
            continue
        # Pseudo-users `unidentified-*` (email aislado de _user_email) no
        # reciben check-in — no son colaboradores reales
        if email_l.startswith("unidentified-"):
            continue
        # Phase U: José recibe el card de ruta, NO el check-in normal
        if email_l in ROUTE_USERS:
            continue
        try:
            ref = ConversationReference().deserialize(ref_dict)

            async def cb(turn_context: TurnContext, _email: str = email) -> None:
                await turn_context.send_activity(_build_checkin_card(_email))

            await activities_adapter.continue_conversation(
                ref, cb, bot_id=ACTIVITIES_APP_ID
            )
            logger.info("Check-in enviado a %s", email)
        except Exception as e:
            logger.exception("Falló check-in a %s: %s", email, e)


def _build_confirmacion_cierre_card(
    emisor_email: str,
    fecha: str,
    sucursal: str,
    total: float,
    entregado: float,
    fondo: float,
    es_recordatorio: bool = False,
) -> Activity:
    """Construye el Adaptive Card que se manda al validador (Daniel/Gabriela)
    para que confirme la recepción del efectivo del cierre.

    Phase P (2026-06-05).
    """
    emisor_alias = emisor_email.split("@")[0]
    fecha_obj = datetime.fromisoformat(fecha).date()
    fecha_humana = fecha_obj.strftime("%d/%m/%Y")

    header_text = (
        f"⏰ RECORDATORIO · Confirmación de cierre pendiente"
        if es_recordatorio
        else f"📥 Confirmación de cierre de caja"
    )
    header_color = "Attention" if es_recordatorio else "Accent"
    intro = (
        f"**{emisor_alias}** ({sucursal}) reportó entrega del cierre del "
        f"**{fecha_humana}**.\n"
        f"¿Confirmás que recibiste este monto?"
    )

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": header_text,
            "size": "Large",
            "weight": "Bolder",
            "color": header_color,
        },
        {
            "type": "TextBlock",
            "text": intro,
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "FactSet",
            "spacing": "Medium",
            "facts": [
                {"title": "Total en caja:", "value": f"${total:,.2f}"},
                {"title": "(–) Fondo fijo:", "value": f"${fondo:,.2f}"},
                {"title": "VALOR A ENTREGAR:", "value": f"${entregado:,.2f}"},
            ],
        },
        {
            "type": "TextBlock",
            "text": "Marca abajo qué pasó:",
            "weight": "Bolder",
            "spacing": "Medium",
        },
        {
            "type": "Input.ChoiceSet",
            "id": "confirm_estado",
            "style": "expanded",
            "value": "confirmado",
            "choices": [
                {"title": f"✅ Sí, recibí exactamente ${entregado:,.2f}", "value": "confirmado"},
                {"title": "⚠️ Recibí un monto distinto (completar abajo)", "value": "discrepancia"},
                {"title": "📝 Pendiente de recibir todavía", "value": "no_recibido"},
            ],
        },
        {
            "type": "Input.Number",
            "id": "confirm_monto",
            "placeholder": "Monto real recibido (solo si fue distinto)",
            "min": 0,
        },
        {
            "type": "Input.Text",
            "id": "confirm_razon",
            "placeholder": "Razón si hay diferencia o si está pendiente (opcional)",
            "isMultiline": True,
        },
    ]

    card_json = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [{
            "type": "Action.Submit",
            "title": "✔️ Confirmar recepción",
            "data": {
                "intent": "confirmar_cierre",
                "emisor_email": emisor_email,
                "fecha": fecha,
                "sucursal": sucursal,
                "entregado": entregado,
            },
        }],
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])


async def send_confirmacion_cierre_to_validador(
    emisor_email: str,
    fecha: str,
    sucursal: str,
    es_recordatorio: bool = False,
) -> bool:
    """Phase V (2026-06-11): DESHABILITADO. Mateo pidió quitar todo el flujo
    de validación porque el cierre de caja es solo del efectivo de la caja
    (no de las ventas del día) — Gabriela Sánchez no debe recibir nada.

    La función se mantiene para no romper imports/llamadas existentes pero
    es no-op."""
    logger.info(
        "send_confirmacion_cierre_to_validador DESHABILITADO (Phase V) "
        "— sucursal=%s emisor=%s fecha=%s", sucursal, emisor_email, fecha,
    )
    return False

    # === código histórico preservado pero inalcanzable ===
    validador = VALIDADOR_CIERRE_POR_CIUDAD.get(sucursal)
    if not validador:
        logger.warning(
            "No hay validador configurado para sucursal %s", sucursal
        )
        return False

    cierre = activity_state.get_cierre_caja(emisor_email, fecha)
    if not cierre:
        logger.warning(
            "No hay cierre de caja para %s en %s", emisor_email, fecha
        )
        return False

    # Inicializar el campo confirmacion en pendiente si no existe
    if not cierre.get("confirmacion"):
        try:
            activity_state.set_cierre_caja_confirmacion(
                emisor_email, fecha,
                estado="pendiente", validador=validador,
            )
        except Exception as e:
            logger.warning("set confirmacion pendiente falló: %s", e)

    refs = _load_refs()
    target_ref_dict = refs.get("activities", {}).get(validador)
    if not target_ref_dict:
        logger.warning(
            "Validador %s no tiene ref del Activities Bot — no se le pudo "
            "mandar el card. Necesita instalar el bot y saludarlo.",
            validador,
        )
        return False

    ref = ConversationReference().deserialize(target_ref_dict)
    card = _build_confirmacion_cierre_card(
        emisor_email=emisor_email,
        fecha=fecha,
        sucursal=sucursal,
        total=cierre["total"],
        entregado=cierre["entregado"],
        fondo=cierre["fondo"],
        es_recordatorio=es_recordatorio,
    )

    async def cb(turn_context: TurnContext, _card: Activity = card) -> None:
        await turn_context.send_activity(_card)

    try:
        await activities_adapter.continue_conversation(
            ref, cb, bot_id=ACTIVITIES_APP_ID
        )
        if es_recordatorio:
            activity_state.add_recordatorio_cierre(emisor_email, fecha)
        logger.info(
            "Card de confirmación cierre %s → %s (recordatorio=%s)",
            emisor_email, validador, es_recordatorio,
        )
        return True
    except Exception as e:
        logger.exception("Falló envío de card confirmación: %s", e)
        return False


async def send_cierre_recordatorios_job() -> None:
    """Phase P: Lun-Vie 8:30 AM busca cierres con confirmación pendiente del
    día anterior (o más viejo) y re-manda card de recordatorio al validador.
    """
    from datetime import date as _date2, timedelta as _td2
    today = _date2.today()
    pendientes = activity_state.get_cierres_caja_pendientes_confirmacion()
    enviados = 0
    for p in pendientes:
        try:
            fecha_obj = _date2.fromisoformat(p["fecha"])
        except (TypeError, ValueError):
            continue
        # Solo recordatorios de cierres del día anterior o más viejo
        if fecha_obj >= today:
            continue
        # Cap: máximo 3 recordatorios para no spamear
        if p.get("recordatorios_enviados", 0) >= 3:
            continue
        sent = await send_confirmacion_cierre_to_validador(
            emisor_email=p["user_email"],
            fecha=p["fecha"],
            sucursal=p["sucursal"],
            es_recordatorio=True,
        )
        if sent:
            enviados += 1
    logger.info("send_cierre_recordatorios_job: %d recordatorios enviados", enviados)


# ============================================================================
# Phase U (2026-06-09) — Card de ruta de envíos para José (asistente 2 GYE)
# ============================================================================


def _refresh_envios_jose(fecha: date | None = None) -> dict[str, Any]:
    """Phase V (2026-06-10): trae envíos de Contifico de AYER + HOY y los
    mergea al snapshot de HOY. También arrastra los pendientes (no entregados)
    de ayer al snapshot de hoy para que José los siga viendo.

    Idempotente: si una factura ya estaba en el snapshot, NO la borra
    ni sobrescribe (preserva entregas marcadas)."""
    from contifico_client import envios_dia_gye

    fecha = fecha or activity_state._today()
    fecha_str = fecha.isoformat()

    # 1) Arrastrar pendientes de AYER al snapshot de HOY
    try:
        activity_state.carry_over_envios_no_entregados(
            JOSE_EMAIL, fecha_hoy=fecha_str
        )
    except Exception as e:
        logger.exception("carry_over envios José falló: %s", e)

    # 2) Pull de Contifico ayer + hoy (dias_atras=1)
    try:
        envios = envios_dia_gye(fecha, dias_atras=1)
    except Exception as e:
        logger.exception("refresh envios José falló: %s", e)
        return {"ok": False, "error": str(e), "total": 0, "nuevos": 0}
    envios_dict = {e["factura_id"]: e for e in envios}
    res = activity_state.set_envios_snapshot(
        JOSE_EMAIL, envios_dict, fecha=fecha_str
    )
    res["ok"] = True
    return res


CAJA_CHICA_ALERTA_ROJO = 30.0  # Phase V: alerta cuando ≤ $30


def _build_jose_ruta_card(
    user_email: str | None = None, skip_refresh: bool = False
) -> Activity:
    """Phase V (2026-06-10): Adaptive Card para José (chofer GYE).

    Estructura:
      1. Header con botón ACTUALIZAR
      2. Estado de ruta: SALIDA/LLEGADA + historial salidas del día
      3. Envíos pendientes (ayer + hoy, carry-over de no entregados)
      4. Caja chica con alerta roja ≤ $30
    """
    email = (user_email or JOSE_EMAIL).lower()
    hoy = activity_state._today()
    hoy_str = hoy.isoformat()
    fecha_label = hoy.strftime("%A %d/%m/%Y")

    # Refrescar snapshot antes de armar el card (skip si ya se hizo refresh
    # explícito desde el handler de Actualizar).
    if not skip_refresh:
        try:
            _refresh_envios_jose(hoy)
        except Exception as e:
            logger.exception("refresh envios José en card build: %s", e)

    try:
        ruta = activity_state.get_ruta_dia(email, hoy_str)
        salidas = ruta.get("salidas", []) or []
        salida_abierta = next(
            (s for s in salidas if not s.get("fin_ts")), None
        )
        entregas_consol = activity_state.get_entregas_consolidadas_dia(
            email, hoy_str
        ) or {}
        cc = activity_state.get_caja_chica(email) or {"inicial": None, "saldo": 0.0, "movimientos": []}
    except Exception as e:
        logger.exception("error leyendo state de José: %s", e)
        ruta = {"salidas": [], "envios_snapshot": {}}
        salidas = []
        salida_abierta = None
        entregas_consol = {}
        cc = {"inicial": None, "saldo": 0.0, "movimientos": []}

    # Helpers
    def _hora_local(iso: str) -> str:
        try:
            from datetime import datetime as _dt
            d = _dt.fromisoformat(iso.replace("Z", "+00:00"))
            return d.astimezone().strftime("%H:%M")
        except Exception:
            return "?"

    def _fmt_fecha_emision(fe: str) -> str:
        """Convierte 'DD/MM/YYYY' (Contifico) o 'YYYY-MM-DD' (ISO) a etiqueta corta."""
        try:
            if "/" in fe:
                d, m, y = fe.split("/")
                from datetime import date as _dt2
                fe_date = _dt2(int(y), int(m), int(d))
            else:
                from datetime import date as _dt2
                fe_date = _dt2.fromisoformat(fe)
            if fe_date == hoy:
                return "HOY"
            from datetime import timedelta as _td2
            if fe_date == hoy - _td2(days=1):
                return "AYER"
            return fe_date.strftime("%d/%m")
        except Exception:
            return fe[:5] if fe else "?"

    # Calcular contadores
    n_entregadas = sum(1 for e in entregas_consol.values() if e.get("status") == "entregado")
    n_no_entregadas = sum(1 for e in entregas_consol.values() if e.get("status") == "no_entregado")
    n_pendientes = sum(1 for e in entregas_consol.values() if e.get("status") == "pendiente")

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": f"🚚 Ruta de José — {fecha_label}",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": (
                f"⏳ Pendientes: **{n_pendientes}**  ·  "
                f"✅ Entregadas: **{n_entregadas}**  ·  "
                f"❌ No entregadas: **{n_no_entregadas}**"
            ),
            "wrap": True,
            "isSubtle": True,
            "spacing": "Small",
        },
    ]

    # ============ BLOQUE 1: Estado de ruta + Salida/Llegada ============
    ruta_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "📍 Estado de ruta",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]

    if salida_abierta:
        ini_local = _hora_local(salida_abierta.get("inicio_ts", ""))
        ruta_items.append({
            "type": "TextBlock",
            "text": f"🟢 **EN RUTA** desde las {ini_local}",
            "wrap": True,
            "color": "Good",
            "weight": "Bolder",
            "size": "Large",
        })
    else:
        ruta_items.append({
            "type": "TextBlock",
            "text": "🏢 **EN OFICINA**",
            "wrap": True,
            "color": "Default",
            "weight": "Bolder",
            "size": "Large",
        })

    # Historial de salidas del día (compacto)
    if salidas:
        hist_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "Salidas de hoy:",
            "weight": "Bolder",
            "isSubtle": True,
            "spacing": "Medium",
            "size": "Small",
        }]
        for i, s in enumerate(salidas, 1):
            if s.get("marcado_en_oficina"):
                continue  # salidas virtuales para entregas en oficina, ocultas
            ini = _hora_local(s.get("inicio_ts", ""))
            fin = _hora_local(s.get("fin_ts", "")) if s.get("fin_ts") else "(en curso)"
            entr_n = sum(
                1 for e in (s.get("entregas") or {}).values()
                if e.get("status") == "entregado"
            )
            hist_items.append({
                "type": "TextBlock",
                "text": f"#{i}: {ini} → {fin}  ({entr_n} entregas)",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "spacing": "None",
            })
        if len(hist_items) > 1:
            ruta_items.append({
                "type": "Container",
                "items": hist_items,
            })

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": ruta_items,
    })

    # ============ BLOQUE 2: Lista de envíos ============
    envios_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "📦 Envíos",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]

    if not entregas_consol:
        envios_items.append({
            "type": "TextBlock",
            "text": "_(No hay envíos pendientes hoy. Apretá 🔄 Actualizar si esperás nuevas facturas.)_",
            "wrap": True,
            "isSubtle": True,
        })
    else:
        # Ordenar: pendientes primero (por fecha asc), después no_entregado, después entregadas
        def _orden(item):
            fid, env = item
            status = env.get("status", "pendiente")
            prio = {"pendiente": 0, "no_entregado": 1, "entregado": 2}.get(status, 3)
            return (prio, env.get("fecha_emision", ""))
        envios_ordenados = sorted(entregas_consol.items(), key=_orden)

        for fid, env in envios_ordenados:
            cliente = env.get("cliente", "?")
            doc = env.get("documento", "?")
            dir_fac = env.get("direccion_factura", "")
            total = env.get("total", 0)
            status = env.get("status", "pendiente")
            dir_real_guardada = env.get("direccion_real", "") or ""
            obs_guardada = env.get("observacion", "") or ""
            razon_guardada = env.get("razon_no_entrega", "") or ""
            fe = env.get("fecha_emision", "")
            badge = _fmt_fecha_emision(fe)

            # Color del Container por estado
            box_style = "default"
            if status == "entregado":
                box_style = "good"
            elif status == "no_entregado":
                box_style = "attention"

            # Phase V: destinos ad-hoc tienen su propio badge
            if env.get("adhoc"):
                tipo_a = (env.get("tipo_adhoc") or "entrega").upper()
                badge_tag = f"➕ {tipo_a}"
                total_str = f"${total:,.2f}" if total > 0 else "(sin monto)"
            else:
                badge_tag = badge
                total_str = f"${total:,.2f}"
            envio_items: list[dict[str, Any]] = [
                {
                    "type": "TextBlock",
                    "text": f"[{badge_tag}] **{cliente}** — {total_str}",
                    "wrap": True,
                    "weight": "Bolder",
                    "size": "Medium",
                },
                {
                    "type": "TextBlock",
                    "text": f"📄 {doc}",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "None",
                    "size": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": f"📍 Dirección factura: {dir_fac or '(sin dirección)'}",
                    "wrap": True,
                    "isSubtle": True,
                    "spacing": "None",
                    "size": "Small",
                },
            ]

            if status == "entregado":
                envio_items.append({
                    "type": "TextBlock",
                    "text": "✅ **ENTREGADO**" + (f" a las {_hora_local(env.get('entrega_ts',''))}" if env.get('entrega_ts') else ""),
                    "color": "Good",
                    "weight": "Bolder",
                    "spacing": "Small",
                })
                if dir_real_guardada:
                    envio_items.append({
                        "type": "TextBlock",
                        "text": f"📍 Entregado en: {dir_real_guardada}",
                        "wrap": True,
                        "isSubtle": True,
                        "spacing": "None",
                        "size": "Small",
                    })
                if env.get("pago_envio"):
                    envio_items.append({
                        "type": "TextBlock",
                        "text": f"💰 Pago al terminal: ${env['pago_envio']:,.2f}",
                        "wrap": True,
                        "isSubtle": True,
                        "spacing": "None",
                        "size": "Small",
                    })
                if obs_guardada:
                    envio_items.append({
                        "type": "TextBlock",
                        "text": f"📝 {obs_guardada}",
                        "wrap": True,
                        "isSubtle": True,
                        "spacing": "None",
                        "size": "Small",
                    })
                envios_items.append({
                    "type": "Container",
                    "style": box_style,
                    "spacing": "Medium",
                    "separator": True,
                    "items": envio_items,
                })
            elif status == "no_entregado":
                envio_items.append({
                    "type": "TextBlock",
                    "text": f"❌ **NO ENTREGADO** — {razon_guardada or 'sin razón'}",
                    "color": "Attention",
                    "weight": "Bolder",
                    "spacing": "Small",
                    "wrap": True,
                })
                # Permitir re-marcar como entregado (botón)
                envios_items.append({
                    "type": "Container",
                    "style": box_style,
                    "spacing": "Medium",
                    "separator": True,
                    "items": envio_items,
                })
                envios_items.append({
                    "type": "ActionSet",
                    "actions": [{
                        "type": "Action.Submit",
                        "title": "🔄 Reintentar (marcar como pendiente)",
                        "data": {"intent": "jose_reintentar_envio", "factura_id": fid},
                    }],
                })
            else:
                # PENDIENTE — inputs + botones
                envio_items.extend([
                    {
                        "type": "Input.ChoiceSet",
                        "id": f"jose_dir_ok_{fid}",
                        "label": "¿La dirección de la factura es correcta?",
                        "style": "expanded",
                        "isMultiSelect": False,
                        "value": "si",
                        "choices": [
                            {"title": "✅ Sí, la dirección está bien", "value": "si"},
                            {"title": "✏️ No, la real es otra", "value": "no"},
                        ],
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_dir_alt_{fid}",
                        "placeholder": "Si dijiste 'No', escribí la dirección real",
                        "value": dir_real_guardada,
                    },
                    {
                        "type": "Input.Number",
                        "id": f"jose_pago_{fid}",
                        "label": "💰 Valor de envío (USD) — opcional",
                        "min": 0,
                        "placeholder": "Ej. 3.60 (se resta de tu caja chica)",
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_obs_{fid}",
                        "label": "📝 Observación — opcional",
                        "placeholder": "Ej. dejado en recepción, llamar antes…",
                        "value": obs_guardada,
                    },
                    {
                        "type": "Input.Text",
                        "id": f"jose_razon_{fid}",
                        "label": "Si NO se pudo entregar, ¿por qué? — opcional",
                        "placeholder": "Ej. no llegaba ikopack, cerrado…",
                    },
                ])
                envios_items.append({
                    "type": "Container",
                    "style": box_style,
                    "spacing": "Medium",
                    "separator": True,
                    "items": envio_items,
                })
                envios_items.append({
                    "type": "ActionSet",
                    "actions": [
                        {
                            "type": "Action.Submit",
                            "title": "✅ Entregado",
                            "style": "positive",
                            "data": {
                                "intent": "jose_marcar_entrega",
                                "factura_id": fid,
                            },
                        },
                        {
                            "type": "Action.Submit",
                            "title": "❌ No entregado",
                            "style": "destructive",
                            "data": {
                                "intent": "jose_marcar_no_entregado",
                                "factura_id": fid,
                            },
                        },
                    ],
                })

    # Sub-bloque al final: añadir destino ad-hoc (cuando José tiene que ir a
    # un lugar no facturado: retiro, encargo extra, devolución, etc.)
    envios_items.extend([
        {
            "type": "TextBlock",
            "text": "➕ Añadir destino o entrega ad-hoc",
            "weight": "Bolder",
            "size": "Medium",
            "spacing": "Large",
            "separator": True,
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": (
                "Si tenés que ir a recoger algo, hacer un envío extra o "
                "cualquier destino que NO esté facturado en Contifico, "
                "agregalo acá."
            ),
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "spacing": "Small",
        },
        {
            "type": "Input.ChoiceSet",
            "id": "jose_adhoc_tipo",
            "label": "Tipo",
            "style": "expanded",
            "isMultiSelect": False,
            "value": "entrega",
            "choices": [
                {"title": "📦 Entrega extra", "value": "entrega"},
                {"title": "↩️ Retiro de mercadería", "value": "retiro"},
                {"title": "📍 Otro destino", "value": "otro"},
            ],
        },
        {
            "type": "Input.Text",
            "id": "jose_adhoc_cliente",
            "label": "Cliente / motivo",
            "placeholder": "Ej. Juan Pérez, retirar bobina, devolución cliente XX",
        },
        {
            "type": "Input.Text",
            "id": "jose_adhoc_direccion",
            "label": "Dirección",
            "placeholder": "Ej. Av. Las Américas N123 y Loja",
        },
        {
            "type": "Input.Number",
            "id": "jose_adhoc_monto",
            "label": "💰 Valor de envío (USD) — opcional",
            "min": 0,
            "placeholder": "Si lo cobras de caja chica, monto",
        },
        {
            "type": "ActionSet",
            "actions": [{
                "type": "Action.Submit",
                "title": "➕ Agregar a la lista",
                "style": "positive",
                "data": {"intent": "jose_add_destino"},
            }],
        },
    ])

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": envios_items,
    })

    # ============ BLOQUE 3: Caja chica ============
    cc_items: list[dict[str, Any]] = [{
        "type": "TextBlock",
        "text": "💵 Caja chica",
        "size": "ExtraLarge",
        "weight": "Bolder",
        "color": "Accent",
    }]
    if cc.get("inicial") is None:
        # Primera vez — pedir saldo inicial
        cc_items.extend([
            {
                "type": "TextBlock",
                "text": (
                    "¿Con cuánto arrancás la caja chica? "
                    "Una sola vez se setea y queda. Después solo registrás gastos y reposiciones."
                ),
                "wrap": True,
                "isSubtle": True,
            },
            {
                "type": "Input.Number",
                "id": "jose_cc_inicial",
                "label": "Saldo inicial (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 20.00",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "💾 Guardar saldo inicial",
                    "style": "positive",
                    "data": {"intent": "jose_caja_inicial"},
                }],
            },
        ])
    else:
        saldo = cc.get("saldo", 0.0)
        # Phase V: alerta roja si ≤ $30
        if saldo <= CAJA_CHICA_ALERTA_ROJO:
            saldo_color = "Attention"  # rojo
            saldo_extra = f"  ⚠️ BAJO — pedí reposición"
        elif saldo <= CAJA_CHICA_ALERTA_ROJO * 2:
            saldo_color = "Warning"  # amarillo
            saldo_extra = ""
        else:
            saldo_color = "Good"  # verde
            saldo_extra = ""
        cc_items.append({
            "type": "TextBlock",
            "text": f"💰 Saldo actual: **${saldo:,.2f}**{saldo_extra}",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": saldo_color,
            "spacing": "Small",
        })
        cc_items.append({
            "type": "TextBlock",
            "text": (
                f"Inicial: ${cc.get('inicial') or 0:,.2f}  ·  "
                f"Movimientos: {len(cc.get('movimientos') or [])}"
            ),
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
            "spacing": "None",
        })

        # Movimientos de HOY (resumen rápido)
        movs_hoy = activity_state.caja_chica_movimientos_dia(email, hoy_str)
        if movs_hoy:
            res_items: list[dict[str, Any]] = []
            for m in movs_hoy[-5:]:  # últimos 5
                sign = "+" if m["tipo"] == "reposicion" else "-"
                color = "Good" if m["tipo"] == "reposicion" else "Attention"
                res_items.append({
                    "type": "TextBlock",
                    "text": (
                        f"{sign}${m['monto']:,.2f} — "
                        f"{m.get('descripcion') or m['tipo']}"
                    ),
                    "color": color,
                    "wrap": True,
                    "spacing": "None",
                })
            cc_items.append({
                "type": "Container",
                "items": [{
                    "type": "TextBlock",
                    "text": "Últimos movimientos de hoy:",
                    "weight": "Bolder",
                    "isSubtle": True,
                    "spacing": "Small",
                }] + res_items,
                "spacing": "Small",
            })

        # Registrar GASTO
        cc_items.extend([
            {
                "type": "TextBlock",
                "text": "➖ Registrar gasto",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
                "color": "Attention",
            },
            {
                "type": "Input.Text",
                "id": "jose_gasto_desc",
                "label": "¿En qué gastaste?",
                "placeholder": "Ej. Envío Reina del Paramo",
            },
            {
                "type": "Input.Number",
                "id": "jose_gasto_monto",
                "label": "Monto (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 3.60",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "➖ Registrar gasto",
                    "style": "destructive",
                    "data": {"intent": "jose_caja_gasto"},
                }],
            },
            {
                "type": "TextBlock",
                "text": "➕ Registrar reposición (cuando Daniel te da más efectivo)",
                "weight": "Bolder",
                "size": "Medium",
                "spacing": "Medium",
                "color": "Good",
            },
            {
                "type": "Input.Number",
                "id": "jose_reposicion_monto",
                "label": "Monto recibido (USD)",
                "min": 0,
                "value": "",
                "placeholder": "Ej. 50.00",
            },
            {
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.Submit",
                    "title": "➕ Sumar reposición",
                    "style": "positive",
                    "data": {"intent": "jose_caja_reposicion"},
                }],
            },
        ])

    body.append({
        "type": "Container",
        "style": "emphasis",
        "spacing": "ExtraLarge",
        "separator": True,
        "bleed": True,
        "items": cc_items,
    })

    # Acciones principales del card: ACTUALIZAR + SALIDA/LLEGADA
    actions: list[dict[str, Any]] = [
        {
            "type": "Action.Submit",
            "title": "🔄 ACTUALIZAR LISTA",
            "data": {"intent": "jose_actualizar"},
        },
    ]
    if salida_abierta:
        actions.append({
            "type": "Action.Submit",
            "title": "🏁 LLEGADA (volví a la oficina)",
            "style": "destructive",
            "data": {"intent": "jose_end_ruta"},
        })
    else:
        actions.append({
            "type": "Action.Submit",
            "title": "▶️ SALIDA (voy a entregar)",
            "style": "positive",
            "data": {"intent": "jose_start_ruta"},
        })

    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": actions,
    }

    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card,
    )
    return Activity(
        type=ActivityTypes.message,
        attachments=[attachment],
    )


async def _handle_jose_intent(
    context: TurnContext,
    intent: str,
    value: dict[str, Any],
    email: str,
) -> None:
    """Maneja todos los intents jose_* del card de ruta."""
    hoy_str = activity_state._today().isoformat()

    if intent == "jose_start_ruta":
        res = activity_state.start_ruta(email, hoy_str)
        if res.get("already_open"):
            await context.send_activity(
                "Ya tenés una ruta abierta. Cuando vuelvas a la oficina apretá "
                "🏁 Volví a la oficina."
            )
        else:
            await context.send_activity(
                f"🟢 **¡Ruta iniciada!** Buen viaje, José. Marcá cada cliente "
                f"cuando entregues. Cuando vuelvas a la oficina apretá 🏁."
            )
        await context.send_activity(_build_jose_ruta_card(email))
        return

    if intent == "jose_add_destino":
        tipo = (value.get("jose_adhoc_tipo") or "entrega").strip().lower()
        cliente = (value.get("jose_adhoc_cliente") or "").strip()
        direccion = (value.get("jose_adhoc_direccion") or "").strip()
        try:
            monto_raw = value.get("jose_adhoc_monto")
            monto = float(monto_raw) if monto_raw not in (None, "", "0") else 0.0
        except (TypeError, ValueError):
            monto = 0.0
        if not cliente:
            await context.send_activity(
                "⚠️ Necesito que pongas el cliente o motivo del destino."
            )
            return
        if not direccion:
            await context.send_activity(
                "⚠️ Necesito que pongas la dirección del destino."
            )
            return
        res = activity_state.add_destino_adhoc(
            JOSE_EMAIL,
            cliente=cliente,
            direccion=direccion,
            tipo=tipo,
            monto=monto,
            fecha=hoy_str,
        )
        tipo_label = {"entrega": "📦 entrega extra", "retiro": "↩️ retiro", "otro": "📍 destino"}.get(tipo, tipo)
        await context.send_activity(
            f"✅ Agregado a tu lista: **{cliente}** ({tipo_label}) — {direccion}"
        )
        await context.send_activity(_build_jose_ruta_card(JOSE_EMAIL, skip_refresh=True))
        return

    if intent == "jose_actualizar":
        # Re-pull de Contifico
        try:
            res = _refresh_envios_jose(activity_state._today())
            n = res.get("total", 0)
            nuevos = res.get("nuevos", 0)
            await context.send_activity(
                f"🔄 Lista actualizada — **{n}** envíos totales"
                + (f" (**{nuevos}** nuevos)" if nuevos > 0 else " (ninguno nuevo)")
                + "."
            )
        except Exception as e:
            await context.send_activity(f"⚠️ No pude actualizar Contifico: {e}")
        await context.send_activity(_build_jose_ruta_card(email, skip_refresh=True))
        return

    if intent == "jose_marcar_entrega":
        factura_id = value.get("factura_id", "")
        dir_ok = (value.get(f"jose_dir_ok_{factura_id}") or "si").strip().lower()
        dir_alt = (value.get(f"jose_dir_alt_{factura_id}") or "").strip()
        direccion_real = dir_alt if dir_ok == "no" and dir_alt else None
        # Phase V: pago_envio + observación
        try:
            pago_raw = value.get(f"jose_pago_{factura_id}")
            pago_envio = float(pago_raw) if pago_raw not in (None, "", "0") else 0.0
        except (TypeError, ValueError):
            pago_envio = 0.0
        observacion = (value.get(f"jose_obs_{factura_id}") or "").strip() or None
        # Buscar cliente_label para descripción del gasto
        snap = activity_state.get_ruta_dia(email, hoy_str).get("envios_snapshot", {}) or {}
        cliente_label = (snap.get(factura_id, {}) or {}).get("cliente", factura_id)
        activity_state.marcar_entrega(
            email, factura_id,
            entregado=True,
            direccion_real=direccion_real,
            pago_envio=pago_envio,
            observacion=observacion,
            cliente_label=cliente_label,
            fecha=hoy_str,
        )
        msg = "✅ Entrega marcada."
        if direccion_real:
            msg += f"\n📍 Dirección real: {direccion_real}"
        if pago_envio > 0:
            cc_now = activity_state.get_caja_chica(email)
            msg += f"\n💰 Pago de ${pago_envio:,.2f} descontado de caja chica (saldo: ${cc_now['saldo']:,.2f})"
        await context.send_activity(msg)
        await context.send_activity(_build_jose_ruta_card(email, skip_refresh=True))
        return

    if intent == "jose_marcar_no_entregado":
        factura_id = value.get("factura_id", "")
        razon = (value.get(f"jose_razon_{factura_id}") or "").strip()
        if not razon:
            await context.send_activity(
                "⚠️ Tenés que explicar por qué no se entregó en el campo "
                "'Si NO se pudo entregar, ¿por qué?' y volver a apretar ❌ No entregado."
            )
            return
        observacion = (value.get(f"jose_obs_{factura_id}") or "").strip() or None
        snap = activity_state.get_ruta_dia(email, hoy_str).get("envios_snapshot", {}) or {}
        cliente_label = (snap.get(factura_id, {}) or {}).get("cliente", factura_id)
        activity_state.marcar_entrega(
            email, factura_id,
            entregado=False,
            razon=razon,
            observacion=observacion,
            cliente_label=cliente_label,
            fecha=hoy_str,
        )
        await context.send_activity(f"❌ Marcado como no entregado: {razon}")
        await context.send_activity(_build_jose_ruta_card(email, skip_refresh=True))
        return

    if intent == "jose_reintentar_envio":
        # Cambia status a pendiente borrando la marca de no_entregado
        factura_id = value.get("factura_id", "")
        st = activity_state.load()
        u = st.get("users", {}).get(email, {})
        for s in (u.get("rutas", {}).get(hoy_str, {}).get("salidas") or []):
            entrs = s.get("entregas", {})
            if factura_id in entrs and entrs[factura_id].get("status") != "entregado":
                entrs.pop(factura_id, None)
        activity_state.save(st)
        await context.send_activity(f"🔄 Envío {factura_id} vuelto a pendiente.")
        await context.send_activity(_build_jose_ruta_card(email, skip_refresh=True))
        return

    if intent == "jose_end_ruta":
        # Recolectar razones de no-entrega de cualquier input que José haya llenado
        razones: dict[str, str] = {}
        for k, v in value.items():
            if k.startswith("jose_razon_no_") and v:
                fid = k.replace("jose_razon_no_", "")
                razones[fid] = str(v).strip()
        res = activity_state.end_ruta(
            email, razones_no_entrega=razones or None, fecha=hoy_str
        )
        if not res.get("ok"):
            await context.send_activity(f"⚠️ {res.get('msg')}")
            return
        dur = res.get("duracion_min", 0) or 0
        entr_count = sum(
            1 for e in res.get("entregas", {}).values()
            if e.get("status") == "entregado"
        )
        no_entr_count = len(res.get("entregas", {})) - entr_count
        msg = (
            f"🏁 **Bienvenido de vuelta, José.**\n\n"
            f"⏱ Duración de la salida: **{dur} min**\n"
            f"✅ Entregadas: **{entr_count}**\n"
        )
        if no_entr_count:
            msg += f"⚠️ Pendientes: **{no_entr_count}**\n"
        msg += "\n_(Tu día se incluye en el resumen del equipo que sale a las 6:30 PM)_"
        await context.send_activity(msg)
        await context.send_activity(_build_jose_ruta_card(email))
        return

    if intent == "jose_caja_inicial":
        try:
            monto = float(value.get("jose_cc_inicial") or 0)
        except (TypeError, ValueError):
            monto = 0
        if monto <= 0:
            await context.send_activity("⚠️ Poné un monto mayor a cero.")
            return
        res = activity_state.set_caja_chica_inicial(email, monto)
        if res.get("ok"):
            await context.send_activity(
                f"💾 Saldo inicial guardado: **${monto:,.2f}**. "
                f"Desde ahora solo registrás gastos y reposiciones."
            )
        else:
            await context.send_activity(
                f"⚠️ Ya tenías saldo inicial guardado (${res.get('inicial', 0):,.2f}). "
                f"Si querés corregirlo, pedile a Mateo que lo resetee."
            )
        await context.send_activity(_build_jose_ruta_card(email))
        return

    if intent == "jose_caja_gasto":
        desc = (value.get("jose_gasto_desc") or "").strip()
        try:
            monto = float(value.get("jose_gasto_monto") or 0)
        except (TypeError, ValueError):
            monto = 0
        if monto <= 0:
            await context.send_activity("⚠️ Poné un monto mayor a cero.")
            return
        if not desc:
            await context.send_activity("⚠️ Poné una descripción del gasto.")
            return
        activity_state.add_caja_chica_movimiento(
            email, "gasto", monto, desc
        )
        cc = activity_state.get_caja_chica(email)
        await context.send_activity(
            f"➖ Gasto registrado: **${monto:,.2f}** ({desc}).\n"
            f"💰 Saldo actual: **${cc['saldo']:,.2f}**"
        )
        await context.send_activity(_build_jose_ruta_card(email))
        return

    if intent == "jose_caja_reposicion":
        try:
            monto = float(value.get("jose_reposicion_monto") or 0)
        except (TypeError, ValueError):
            monto = 0
        if monto <= 0:
            await context.send_activity("⚠️ Poné un monto mayor a cero.")
            return
        activity_state.add_caja_chica_movimiento(
            email, "reposicion", monto, "Reposición de caja"
        )
        cc = activity_state.get_caja_chica(email)
        await context.send_activity(
            f"➕ Reposición de **${monto:,.2f}** sumada a caja chica.\n"
            f"💰 Saldo actual: **${cc['saldo']:,.2f}**"
        )
        await context.send_activity(_build_jose_ruta_card(email))
        return


async def send_jose_route_card_job() -> None:
    """Job de scheduler: envía el card de ruta a José.
    Se dispara dos veces al día (11 AM y 3 PM EC, Lun-Sáb)."""
    refs = _load_refs()
    ref_dict = refs.get("activities", {}).get(JOSE_EMAIL)
    if not ref_dict:
        logger.warning("send_jose_route_card_job: no hay ref para %s", JOSE_EMAIL)
        return
    try:
        ref = ConversationReference().deserialize(ref_dict)

        async def cb(turn_context: TurnContext) -> None:
            await turn_context.send_activity(
                "🚚 Actualicé tu lista de envíos. Revisá el card y arrancá cuando salgas."
            )
            await turn_context.send_activity(_build_jose_ruta_card(JOSE_EMAIL))

        await activities_adapter.continue_conversation(
            ref, cb, bot_id=ACTIVITIES_APP_ID
        )
        logger.info("Card de ruta enviado a José")
    except Exception as e:
        logger.exception("Falló send_jose_route_card_job: %s", e)


def _jose_summary_html(fecha: str | None = None) -> str:
    """Construye el HTML del email resumen del día de José (6:30 PM)."""
    from html import escape
    fecha = fecha or activity_state._today().isoformat()
    ruta = activity_state.get_ruta_dia(JOSE_EMAIL, fecha)
    salidas = ruta.get("salidas", []) or []
    entregas = activity_state.get_entregas_consolidadas_dia(JOSE_EMAIL, fecha)
    cc = activity_state.get_caja_chica(JOSE_EMAIL)
    movs_hoy = activity_state.caja_chica_movimientos_dia(JOSE_EMAIL, fecha)
    fecha_fmt = date.fromisoformat(fecha).strftime("%d/%m/%Y")

    # Tabla de entregas
    rows = []
    total_entregadas = 0
    total_pendientes = 0
    for fid, env in entregas.items():
        status = env.get("status", "pendiente")
        if status == "entregado":
            total_entregadas += 1
            badge = "<span style='color:#0d8a3f;font-weight:600'>✅ Entregado</span>"
        elif status == "no_entregado":
            total_pendientes += 1
            badge = "<span style='color:#c53030;font-weight:600'>❌ No entregado</span>"
        else:
            total_pendientes += 1
            badge = "<span style='color:#999'>⏳ Pendiente</span>"
        dir_final = env.get("direccion_real") or env.get("direccion_factura") or ""
        razon = env.get("razon_no_entrega") or ""
        cliente = env.get("cliente", "")
        doc = env.get("documento", "")
        total = env.get("total", 0)
        razon_html = ""
        if razon:
            razon_html = "<br><small style='color:#777'>" + escape(razon) + "</small>"
        rows.append(
            f"<tr>"
            f"<td>{escape(cliente)}<br><small style='color:#777'>{escape(doc)}</small></td>"
            f"<td style='text-align:right'>${total:,.2f}</td>"
            f"<td>{escape(dir_final)}</td>"
            f"<td>{badge}{razon_html}</td>"
            f"</tr>"
        )
    if not rows:
        rows.append(
            "<tr><td colspan='4' style='color:#999;text-align:center'>"
            "Sin envíos hoy.</td></tr>"
        )
    tabla_entregas = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<tr style='background:#f0f0f0'>"
        "<th style='padding:8px;text-align:left'>Cliente</th>"
        "<th style='padding:8px;text-align:right'>Monto</th>"
        "<th style='padding:8px;text-align:left'>Dirección final</th>"
        "<th style='padding:8px;text-align:left'>Estado</th>"
        "</tr>"
        + "".join(rows) + "</table>"
    )

    # Bloque de salidas (tiempos)
    salidas_html = ""
    for i, s in enumerate(salidas, 1):
        ini = s.get("inicio_ts", "")[:16].replace("T", " ")
        fin = s.get("fin_ts") or "(EN RUTA)"
        if s.get("fin_ts"):
            fin = s["fin_ts"][:16].replace("T", " ")
            try:
                from datetime import datetime as _dt
                d1 = _dt.fromisoformat(s["inicio_ts"].replace("Z", "+00:00"))
                d2 = _dt.fromisoformat(s["fin_ts"].replace("Z", "+00:00"))
                dur = int((d2 - d1).total_seconds() / 60)
            except Exception:
                dur = "?"
        else:
            dur = "—"
        entr_n = sum(1 for e in (s.get("entregas") or {}).values()
                     if e.get("status") == "entregado")
        salidas_html += (
            f"<tr>"
            f"<td>Salida #{i}</td>"
            f"<td>{ini}</td>"
            f"<td>{fin}</td>"
            f"<td>{dur} min</td>"
            f"<td>{entr_n} entregas</td>"
            f"</tr>"
        )
    if not salidas_html:
        salidas_html = (
            "<tr><td colspan='5' style='color:#999;text-align:center'>"
            "José no salió a ruta hoy.</td></tr>"
        )
    tabla_salidas = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<tr style='background:#f0f0f0'>"
        "<th style='padding:8px;text-align:left'>#</th>"
        "<th style='padding:8px;text-align:left'>Inicio</th>"
        "<th style='padding:8px;text-align:left'>Fin</th>"
        "<th style='padding:8px;text-align:left'>Duración</th>"
        "<th style='padding:8px;text-align:left'>Entregas</th>"
        "</tr>"
        + salidas_html + "</table>"
    )

    # Caja chica
    cc_rows = []
    total_gastos = 0.0
    total_repos = 0.0
    for m in movs_hoy:
        sign = "+" if m["tipo"] == "reposicion" else "-"
        color = "#0d8a3f" if m["tipo"] == "reposicion" else "#c53030"
        if m["tipo"] == "reposicion":
            total_repos += float(m.get("monto", 0))
        else:
            total_gastos += float(m.get("monto", 0))
        ts = m.get("ts", "")[:16].replace("T", " ")
        cc_rows.append(
            f"<tr>"
            f"<td>{ts}</td>"
            f"<td>{escape(m.get('descripcion', '') or m['tipo'])}</td>"
            f"<td style='text-align:right;color:{color};font-weight:600'>"
            f"{sign}${m['monto']:,.2f}</td>"
            f"</tr>"
        )
    if not cc_rows:
        cc_rows.append(
            "<tr><td colspan='3' style='color:#999;text-align:center'>"
            "Sin movimientos hoy.</td></tr>"
        )
    tabla_caja = (
        "<table style='width:100%;border-collapse:collapse;font-size:14px'>"
        "<tr style='background:#f0f0f0'>"
        "<th style='padding:8px;text-align:left'>Hora</th>"
        "<th style='padding:8px;text-align:left'>Descripción</th>"
        "<th style='padding:8px;text-align:right'>Monto</th>"
        "</tr>"
        + "".join(cc_rows) + "</table>"
    )

    return f"""
    <html><body style='font-family:Arial,Helvetica,sans-serif;color:#222'>
    <h2 style='color:#2d7d3f'>🚚 Resumen del día — José Solórzano</h2>
    <p style='color:#666'>{fecha_fmt} (asistente 2 GYE)</p>

    <h3 style='margin-top:24px'>📊 Resumen</h3>
    <p>
      ✅ <b>{total_entregadas}</b> entregas completadas<br>
      ⏳ <b>{total_pendientes}</b> pendientes / no entregadas<br>
      🚗 <b>{len(salidas)}</b> salidas hoy
    </p>

    <h3 style='margin-top:24px'>📦 Entregas del día</h3>
    {tabla_entregas}

    <h3 style='margin-top:24px'>⏱ Salidas y tiempos</h3>
    {tabla_salidas}

    <h3 style='margin-top:24px'>💵 Caja chica</h3>
    <p>
      Saldo inicial (histórico): <b>${cc.get('inicial') or 0:,.2f}</b>  ·
      Gastos del día: <b style='color:#c53030'>-${total_gastos:,.2f}</b>  ·
      Reposiciones del día: <b style='color:#0d8a3f'>+${total_repos:,.2f}</b><br>
      <b>Saldo actual: ${cc.get('saldo', 0):,.2f}</b>
    </p>
    {tabla_caja}

    <hr style='margin-top:32px;border:none;border-top:1px solid #ddd'>
    <p style='color:#999;font-size:12px'>
      Generado automáticamente por el Activities Bot · {fecha_fmt}
    </p>
    </body></html>
    """


async def send_jose_summary_email_job() -> None:
    """Job de scheduler: envía el resumen del día de José a Daniel + Mateo.
    Se dispara a las 6:30 PM EC, Lun-Sáb."""
    fecha = activity_state._today().isoformat()
    try:
        html_body = _jose_summary_html(fecha)
        subject = f"🚚 Resumen del día — José Solórzano (GYE) — {activity_state._today().strftime('%d/%m/%Y')}"
        from graph_mail import send as graph_send
        graph_send(
            from_user=JOSE_EMAIL,  # remitente
            to=JOSE_SUMMARY_TO,
            subject=subject,
            html_body=html_body,
        )
        logger.info("Resumen de José enviado a %s", JOSE_SUMMARY_TO)
    except Exception as e:
        logger.exception("send_jose_summary_email_job falló: %s", e)


# ============================================================
# Fase 3 (2026-06-12): infraestructura de ENTREGA CONFIABLE.
# Generaliza el patrón retry+alerta que solo tenía morning_sales (auditoría:
# "solo 1 de ~13 jobs que envían algo tiene reintentos+alerta") y agrega el
# ledger de envíos: un reporte (key, fecha) se envía EXACTAMENTE una vez.
# ============================================================
JOB_RETRY_ATTEMPTS = 3
JOB_RETRY_WAIT = 60  # segundos entre intentos
ALERT_EMAIL = os.environ.get(
    "ALERT_EMAIL", "malvarado@biodegradablesecuador.com"
).strip()


def _send_job_failure_alert(job_name: str, error_msg: str, attempts: int) -> None:
    """Alerta por correo cuando un job agotó todos los reintentos.
    NO falla si la alerta falla (best effort + log)."""
    try:
        import graph_mail as _gm
        _gm.send(
            from_user=ALERT_EMAIL,
            to=ALERT_EMAIL,
            subject=f"⚠️ Job {job_name} FALLÓ tras {attempts} intentos",
            html_body=(
                f"<h2 style='color:#c53030'>⚠️ {job_name} falló</h2>"
                f"<p>El job <code>{job_name}</code> intentó <b>{attempts}</b> "
                f"veces y falló todas. El correo/acción de hoy NO salió.</p>"
                f"<p><b>Último error:</b><br><pre style='background:#f5f5f5;"
                f"padding:10px;border-radius:4px;font-size:12px'>"
                f"{error_msg[:1500]}</pre></p>"
                f"<p>Revisar logs del App Service y disparar manual con el "
                f"endpoint admin correspondiente.</p>"
            ),
        )
        logger.warning("Alerta de fallo de %s enviada a %s", job_name, ALERT_EMAIL)
    except Exception as alert_err:
        logger.exception("La alerta de %s también falló: %s", job_name, alert_err)


async def _reliable_job(
    job_name: str,
    fn,
    *,
    ledger_key: str | None = None,
    retries: int = JOB_RETRY_ATTEMPTS,
    wait: int = JOB_RETRY_WAIT,
    alert: bool = True,
) -> bool:
    """Ejecuta un job con: ledger anti-duplicado + reintentos + alerta.

    `fn` puede ser async o sync (sync corre en thread). Devuelve True si
    el job completó. Semántica del ledger:
    - claim() antes de ejecutar — si otro worker/capa ya lo envió hoy o lo
      está enviando, NO se ejecuta (anti-duplicado, auditoría S1/S9).
    - confirm() al completar; release() al fallar (permite el reintento del
      catch-up o de un disparo manual).
    """
    fecha = send_ledger.today_iso()
    if ledger_key and not send_ledger.claim(ledger_key, fecha):
        logger.info("%s: skip — ya enviado/en curso para %s", job_name, fecha)
        return False

    ok = False
    last_err = ""
    try:
        for attempt in range(1, retries + 1):
            try:
                # Convención: fn devuelve una corutina (función async o
                # lambda que envuelve un sync con asyncio.to_thread). Un
                # sync directo bloquearía el event loop.
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
                ok = True
                logger.info("%s: completado (intento %d/%d)", job_name, attempt, retries)
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                logger.exception(
                    "%s: intento %d/%d falló: %s", job_name, attempt, retries, e
                )
                if attempt < retries:
                    await asyncio.sleep(wait)
    finally:
        if ledger_key:
            if ok:
                send_ledger.confirm(ledger_key, fecha)
            else:
                send_ledger.release(ledger_key, fecha)

    if not ok:
        logger.error("%s: TODOS los %d intentos fallaron: %s", job_name, retries, last_err)
        if alert:
            try:
                await asyncio.to_thread(
                    _send_job_failure_alert, job_name, last_err, retries
                )
            except Exception:
                logger.exception("No se pudo despachar la alerta de %s", job_name)
    return ok


def _run_daily_report_morning() -> None:
    """Corre daily_report.main() en modo morning (sync, para to_thread)."""
    import sys as _sys
    _orig = _sys.argv
    _sys.argv = ["teams_bot", "morning"]
    try:
        import importlib
        import daily_report
        importlib.reload(daily_report)  # state más fresco
        result = daily_report.main()
        if result != 0:
            raise RuntimeError(f"daily_report.main retornó exit_code={result}")
    finally:
        _sys.argv = _orig


async def send_morning_sales_report_job() -> None:
    """Reporte comercial Lun-Sáb 8:00 EC — vía _reliable_job + ledger."""
    await _reliable_job(
        "morning_sales_report",
        lambda: asyncio.to_thread(_run_daily_report_morning),
        ledger_key="morning_sales",
    )


def _build_apertura_caja_card(user_email: str) -> Activity:
    """Phase S+ (2026-06-09): card matinal 8:15 AM con resumen de actividades
    del día (recordatorio informativo, sin inputs ni submit). Para info@, quito@
    y gsanchez@.
    """
    fecha_humana = datetime.now(activity_state.LOCAL_TZ).strftime("%A %d/%m/%Y")

    # Activities del día
    wk = activity_state.get_week(user_email)
    diarias = [(aid, a) for aid, a in wk["activities"].items()
               if a["tipo"] == "diaria"]
    cobranzas = [(aid, a) for aid, a in diarias if aid.startswith("cobranza-")]
    otras_diarias = [(aid, a) for aid, a in diarias if not aid.startswith("cobranza-")]
    semanales = [(aid, a) for aid, a in wk["activities"].items()
                 if a["tipo"] != "diaria"]

    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": "☀️ Buen día — tu agenda de hoy",
            "size": "ExtraLarge",
            "weight": "Bolder",
            "color": "Accent",
        },
        {
            "type": "TextBlock",
            "text": fecha_humana.capitalize(),
            "spacing": "None",
            "isSubtle": True,
        },
    ]

    # Phase S+ (2026-06-09): cada sección en su Container para separación visual
    # Cobranzas (si tiene)
    if cobranzas:
        cob_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": f"📞 {len(cobranzas)} cobranzas para contactar",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in cobranzas:
            nombre = a.get("nombre", "").replace("📞 Cobranza:", "").strip()
            cob_items.append({
                "type": "TextBlock",
                "text": f"• {nombre}",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": cob_items,
        })

    # Otras actividades diarias
    if otras_diarias:
        od_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📅 Actividades diarias",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in otras_diarias:
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            meta = a.get("meta")
            unidad = a.get("unidad", "")
            meta_txt = f" (meta {meta} {unidad})" if meta else ""
            od_items.append({
                "type": "TextBlock",
                "text": f"• {prio_badge}{a.get('nombre', aid)}{meta_txt}",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": od_items,
        })

    # Proyectos semanales (resumen breve)
    if semanales:
        sem_items: list[dict[str, Any]] = [{
            "type": "TextBlock",
            "text": "📌 Proyectos semanales en curso",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
        }]
        for aid, a in semanales:
            priority = a.get("priority", "media")
            prio_badge = {"alta": "🔴 ", "media": "", "baja": "⚪ "}.get(priority, "")
            avance = a.get("avance", 0) or 0
            sem_items.append({
                "type": "TextBlock",
                "text": f"• {prio_badge}{a.get('nombre', aid)} — {avance:.0f}%",
                "wrap": True,
                "isSubtle": True,
                "spacing": "None",
            })
        body.append({
            "type": "Container",
            "style": "emphasis",
            "spacing": "Large",
            "separator": True,
            "items": sem_items,
        })

    if not cobranzas and not otras_diarias and not semanales:
        body.append({
            "type": "TextBlock",
            "text": "Hoy no tenés actividades asignadas todavía. ¡Buen día!",
            "wrap": True,
            "isSubtle": True,
            "spacing": "Medium",
        })

    # Footer
    body.append({
        "type": "TextBlock",
        "text": (
            "Tu check-in con el detalle de lo hecho lo hacés a la tarde "
            "(4:30 PM Mateo/Gabriela · 5:00 PM Gladys/Gabriela Bravo). "
            "¡Buen día y mucha suerte!"
        ),
        "isSubtle": True,
        "wrap": True,
        "spacing": "Large",
        "size": "Small",
    })

    card_json = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
    }
    attachment = Attachment(
        content_type="application/vnd.microsoft.card.adaptive",
        content=card_json,
    )
    return Activity(type=ActivityTypes.message, attachments=[attachment])


RECORDATORIO_MATINAL_USERS = CIERRE_CAJA_USERS | {
    "gsanchez@biodegradablesecuador.com",
}


async def send_apertura_caja_matinal_job() -> None:
    """Phase S+ (2026-06-09): 8:15 AM Lun-Vie EC manda recordatorio de
    actividades del día a info@, quito@ y gsanchez@. Ya no incluye apertura
    de caja con denominaciones — solo resumen informativo.
    """
    refs = _load_refs()
    activities_refs = refs.get("activities", {})
    enviadas = 0
    for email in RECORDATORIO_MATINAL_USERS:
        ref_dict = activities_refs.get(email)
        if not ref_dict:
            logger.warning(
                "recordatorio matinal: %s no tiene ref del Activities Bot", email
            )
            continue
        try:
            ref = ConversationReference().deserialize(ref_dict)

            async def cb(turn_context: TurnContext, _email: str = email) -> None:
                await turn_context.send_activity(_build_apertura_caja_card(_email))

            await activities_adapter.continue_conversation(
                ref, cb, bot_id=ACTIVITIES_APP_ID
            )
            enviadas += 1
            logger.info("Recordatorio matinal enviado a %s", email)
        except Exception as e:
            logger.exception("Falló recordatorio matinal a %s: %s", email, e)
    logger.info("send_apertura_caja_matinal: %d enviadas", enviadas)


async def send_consolidated_daily_summary_job() -> None:
    """Phase O: a las 18:30 EC manda UN solo correo a Daniel+Gabriela con
    el resumen de todos los colaboradores no-supervisor. Reemplaza emails
    individuales."""
    try:
        # Lazy import para evitar circulares
        from ask_agent import _send_consolidated_daily_summary
        result = await asyncio.to_thread(_send_consolidated_daily_summary)
        logger.info(
            "Consolidated daily summary OK → to=%s cc=%s collabs=%s",
            result.get("to"), result.get("cc"), result.get("collaborators"),
        )
    except Exception as e:
        logger.exception("Falló consolidated daily summary: %s", e)


# ===== Scheduler =====
EC_TZ = pytz.timezone("America/Guayaquil")
# Fase 3 (auditoría S2): misfire_grace_time era el default de 1 segundo — un
# deploy/restart a la hora exacta de un job perdía la ejecución en silencio.
# Con 1h de gracia + coalesce, una ejecución atrasada corre apenas el
# scheduler revive (y el ledger evita duplicados si ya había salido).
scheduler = AsyncIOScheduler(
    timezone=EC_TZ,
    job_defaults={"coalesce": True, "misfire_grace_time": 3600},
)


# --- Jobs envueltos en _reliable_job (ledger + retry + alerta) ---
# Check-in cards: destinatarios/horarios en core_config.CHECKIN_*. Un fallo
# por destinatario NO afecta a los demás (try/except por user dentro de
# send_daily_checkin). El ledger evita duplicados por día y job.
def _checkin_override_users_hoy() -> set[str]:
    """Usuarios con horario override HOY (core_config.CHECKIN_DATE_OVERRIDES).
    El job regular de su grupo los omite ese día — el override los cubre."""
    entries = core_config.CHECKIN_DATE_OVERRIDES.get(send_ledger.today_iso(), [])
    return {u.lower() for _hm, users in entries for u in users}


async def _job_checkin_oficina() -> None:
    targets = {u.lower() for u in core_config.CHECKIN_OFICINA} - _checkin_override_users_hoy()
    if not targets:
        logger.info("checkin_oficina: todos con override hoy — skip")
        return
    await _reliable_job(
        "checkin_oficina",
        lambda: send_daily_checkin(only=targets),
        ledger_key="checkin_oficina",
    )


async def _job_checkin_sucursales() -> None:
    targets = {u.lower() for u in core_config.CHECKIN_SUCURSALES} - _checkin_override_users_hoy()
    if not targets:
        logger.info("checkin_sucursales: todos con override hoy — skip")
        return
    await _reliable_job(
        "checkin_sucursales",
        lambda: send_daily_checkin(only=targets),
        ledger_key="checkin_sucursales",
    )


async def _job_checkin_override(hhmm: str, users: list[str]) -> None:
    await _reliable_job(
        f"checkin_override_{hhmm}",
        lambda: send_daily_checkin(only={u.lower() for u in users}),
        ledger_key=f"checkin_override_{hhmm}",
    )


async def _job_weekly_summaries() -> None:
    await _reliable_job(
        "weekly_summaries", send_weekly_summaries, ledger_key="weekly_summaries"
    )


async def _job_consolidated_daily() -> None:
    await _reliable_job(
        "consolidated_daily_summary",
        send_consolidated_daily_summary_job,
        ledger_key="consolidated_daily",
    )


async def _job_monthly_sales_recap() -> None:
    # Fase 3 (auditoría S4): los recaps estaban registrados SIN wrapper —
    # un fallo el día 1 se perdía un mes entero sin alerta.
    await _reliable_job(
        "monthly_sales_recap",
        lambda: asyncio.to_thread(monthly_recap.send_sales_recap),
        ledger_key="monthly_sales_recap",
    )


async def _job_monthly_activities_recap() -> None:
    await _reliable_job(
        "monthly_activities_recap",
        lambda: asyncio.to_thread(monthly_recap.send_activities_recap),
        ledger_key="monthly_activities_recap",
    )


def _run_logistics_morning() -> None:
    """Corre daily_logistics_report.main() en modo morning (para to_thread)."""
    import sys as _sys
    _orig = _sys.argv
    _sys.argv = ["teams_bot", "morning"]
    try:
        import importlib
        import daily_logistics_report
        importlib.reload(daily_logistics_report)
        result = daily_logistics_report.main()
        if result not in (0, None):
            raise RuntimeError(f"daily_logistics_report.main retornó {result}")
    finally:
        _sys.argv = _orig


async def _job_logistics_morning() -> None:
    await _reliable_job(
        "logistics_morning",
        lambda: asyncio.to_thread(_run_logistics_morning),
        ledger_key="logistics_morning",
    )


def _schedule_jobs() -> None:
    # ===== Check-in cards — config única en core_config.CHECKIN_* =====
    # Lun-Vie: oficina 16:30, sucursales 17:10. Sáb: SOLO sucursales 12:30.
    # Domingo: NINGÚN envío (ningún trigger lo cubre).
    oh, om = core_config.CHECKIN_WEEKDAY_OFICINA
    scheduler.add_job(
        _job_checkin_oficina,
        CronTrigger(day_of_week="mon-fri", hour=oh, minute=om, timezone=EC_TZ),
        id="checkin_weekday",
        replace_existing=True,
    )
    sh, sm = core_config.CHECKIN_WEEKDAY_SUCURSALES
    scheduler.add_job(
        _job_checkin_sucursales,
        CronTrigger(day_of_week="mon-fri", hour=sh, minute=sm, timezone=EC_TZ),
        id="checkin_sucursales_weekday",
        replace_existing=True,
    )
    bh, bm = core_config.CHECKIN_SATURDAY_SUCURSALES
    scheduler.add_job(
        _job_checkin_sucursales,
        CronTrigger(day_of_week="sat", hour=bh, minute=bm, timezone=EC_TZ),
        id="checkin_saturday",
        replace_existing=True,
    )
    # Overrides puntuales por fecha (solo hoy en adelante). Ese día el job
    # regular omite a los usuarios del override (_checkin_override_users_hoy).
    _hoy_iso = send_ledger.today_iso()
    for _fecha_iso, _entries in core_config.CHECKIN_DATE_OVERRIDES.items():
        if _fecha_iso < _hoy_iso:
            continue
        _y, _mo, _d = (int(p) for p in _fecha_iso.split("-"))
        for (_h, _m), _users in _entries:
            scheduler.add_job(
                _job_checkin_override,
                CronTrigger(
                    year=_y, month=_mo, day=_d, hour=_h, minute=_m, timezone=EC_TZ
                ),
                id=f"checkin_override_{_fecha_iso}_{_h:02d}{_m:02d}",
                replace_existing=True,
                args=[f"{_h:02d}{_m:02d}", list(_users)],
            )
    # Reminders: cada 5 min, chequea si hay reminders vencidos para entregar
    scheduler.add_job(
        deliver_due_reminders,
        CronTrigger(minute="*/5", timezone=EC_TZ),
        id="deliver_reminders",
        replace_existing=True,
    )
    # Auto-asignación de cobranzas: Lun-Vie 7:30 AM EC (antes del daily report)
    scheduler.add_job(
        auto_assign_cobranzas,
        CronTrigger(day_of_week="mon-fri", hour=7, minute=30, timezone=EC_TZ),
        id="auto_assign_cobranzas",
        replace_existing=True,
    )
    # Weekly summaries: viernes 17:00 EC (cierre de semana laboral)
    scheduler.add_job(
        _job_weekly_summaries,
        CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=EC_TZ),
        id="weekly_summaries",
        replace_existing=True,
    )
    # Daily news brief: 6 AM EC, antes del daily report y de queries de gerencia
    scheduler.add_job(
        generate_daily_news_brief,
        CronTrigger(hour=6, minute=0, timezone=EC_TZ),
        id="daily_news_brief",
        replace_existing=True,
    )
    # Phase M — Monthly recaps día 1: full recap mes anterior + proyección
    scheduler.add_job(
        _job_monthly_sales_recap,
        CronTrigger(day=1, hour=9, minute=0, timezone=EC_TZ),
        id="monthly_sales_recap_day1",
        replace_existing=True,
    )
    scheduler.add_job(
        _job_monthly_activities_recap,
        CronTrigger(day=1, hour=10, minute=0, timezone=EC_TZ),
        id="monthly_activities_recap_day1",
        replace_existing=True,
    )
    # Phase M Quincenal — DESHABILITADO 2026-06-02 por feedback de Mateo
    # ("no me gustó el quincenal, ahorita no es necesario"). El endpoint
    # /admin/trigger-midmonth-status sigue disponible para disparo manual.

    # Phase S (2026-06-08): apertura de caja matinal Lun-Vie 8:15 AM EC
    scheduler.add_job(
        send_apertura_caja_matinal_job,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=15, timezone=EC_TZ),
        id="apertura_caja_matinal",
        replace_existing=True,
    )

    # Phase P (2026-06-05): recordatorio de confirmación de cierre Lun-Vie 8:30 AM EC.
    # Phase V (2026-06-11): DESHABILITADO. Mateo pidió quitar todo el flujo
    # de validación de cierre porque solo se cierra con efectivo de caja, no
    # con ventas del día — Gabriela Sánchez no debe recibir nada.
    # scheduler.add_job(
    #     send_cierre_recordatorios_job,
    #     CronTrigger(day_of_week="mon-fri", hour=8, minute=30, timezone=EC_TZ),
    #     id="cierre_recordatorios_morning",
    #     replace_existing=True,
    # )
    # Phase O (2026-06-02): consolidated daily summary Lun-Vie 18:30 EC
    # — un solo correo a Daniel+Gabriela con TODOS los colaboradores.
    # Reemplaza los emails individuales que se mandaban al hacer check-in.
    scheduler.add_job(
        _job_consolidated_daily,
        CronTrigger(day_of_week="mon-fri", hour=18, minute=30, timezone=EC_TZ),
        id="consolidated_daily_summary",
        replace_existing=True,
    )

    # Phase U (2026-06-09): José — card on-demand cuando él escribe al bot,
    # NO en horario fijo.
    # Phase V (2026-06-10): el resumen del día de José YA NO se envía como
    # correo aparte — está integrado en el consolidated_daily_summary 18:30
    # como bloque "📦 ASISTENTE 2 GYE — José Solórzano".

    # Phase U+ (2026-06-10): morning_sales_report al bot 24/7 (Lun-Sáb 8:00 EC).
    # Reemplaza el timer del azfunc (que se dormía en Consumption Plan).
    scheduler.add_job(
        send_morning_sales_report_job,
        CronTrigger(day_of_week="mon-sat", hour=8, minute=0, timezone=EC_TZ),
        id="morning_sales_report",
        replace_existing=True,
    )

    # Fase 3 (auditoría S5): logística migrada al bot, como se hizo con el
    # comercial — sale del Consumption Plan que se dormía y gana ledger +
    # retry + alerta. CUTOVER: activar LOGISTICS_IN_BOT=1 en el App Service
    # SOLO junto con AzureWebJobs.logistics_morning.Disabled=true en el
    # Function App (si ambos corren, los ledgers viven en universos
    # distintos y Gabriela recibiría dos correos).
    if os.environ.get("LOGISTICS_IN_BOT", "0").strip() == "1":
        scheduler.add_job(
            _job_logistics_morning,
            CronTrigger(day_of_week="mon-sat", hour=8, minute=0, timezone=EC_TZ),
            id="logistics_morning",
            replace_existing=True,
        )
        logger.info("logistics_morning programado EN EL BOT (LOGISTICS_IN_BOT=1)")

    logger.info(
        "Jobs: checkin oficina mon-fri 16:30, sucursales mon-fri 17:10 + "
        "sat 12:30 (domingo NADA), reminders */5min, "
        "cobranzas mon-fri 7:30, weekly_summaries fri 17:00, "
        "news_brief daily 6:00, monthly_recaps day 1 9:00+10:00, "
        "consolidated_daily mon-fri 18:30, "
        "jose_summary mon-sat 18:30 (card on-demand)"
    )


# ===== Catch-up de envíos perdidos (Fase 3, auditoría S2) =====
# Si el bot estuvo caído (deploy, restart) a la hora de un reporte, al
# arrancar revisa el ledger del día y dispara lo que no salió. El ledger
# evita duplicados si sí había salido.
def _catchup_specs() -> list[tuple[str, Any, Any]]:
    specs: list[tuple[str, Any, Any]] = [
        ("morning_sales", send_morning_sales_report_job,
         lambda now: now.weekday() <= 5 and (now.hour, now.minute) >= (8, 0)),
        ("consolidated_daily", _job_consolidated_daily,
         lambda now: now.weekday() <= 4 and (now.hour, now.minute) >= (18, 30)),
        ("weekly_summaries", _job_weekly_summaries,
         lambda now: now.weekday() == 4 and (now.hour, now.minute) >= (17, 0)),
        ("monthly_sales_recap", _job_monthly_sales_recap,
         lambda now: now.day == 1 and (now.hour, now.minute) >= (9, 0)),
        ("monthly_activities_recap", _job_monthly_activities_recap,
         lambda now: now.day == 1 and (now.hour, now.minute) >= (10, 0)),
        # Check-ins: lun-vie oficina y sucursales; sáb solo sucursales.
        # Domingo (weekday 6) ninguna condición aplica — no hay catch-up.
        ("checkin_oficina", _job_checkin_oficina,
         lambda now: now.weekday() <= 4
         and (now.hour, now.minute) >= core_config.CHECKIN_WEEKDAY_OFICINA),
        ("checkin_sucursales", _job_checkin_sucursales,
         lambda now: (now.weekday() <= 4
                      and (now.hour, now.minute) >= core_config.CHECKIN_WEEKDAY_SUCURSALES)
         or (now.weekday() == 5
             and (now.hour, now.minute) >= core_config.CHECKIN_SATURDAY_SUCURSALES)),
    ]
    # Overrides de check-in de HOY: si el bot estuvo caído a esa hora, el
    # catch-up los dispara al arrancar (el ledger evita duplicados).
    for (_h, _m), _users in core_config.CHECKIN_DATE_OVERRIDES.get(
        send_ledger.today_iso(), []
    ):
        _hhmm = f"{_h:02d}{_m:02d}"
        specs.append((
            f"checkin_override_{_hhmm}",
            lambda _hh=_hhmm, _us=_users: _job_checkin_override(_hh, list(_us)),
            lambda now, _h=_h, _m=_m: (now.hour, now.minute) >= (_h, _m),
        ))
    if os.environ.get("LOGISTICS_IN_BOT", "0").strip() == "1":
        specs.append(
            ("logistics_morning", _job_logistics_morning,
             lambda now: now.weekday() <= 5 and (now.hour, now.minute) >= (8, 0))
        )
    return specs


async def _catchup_missed_sends() -> None:
    # Primer arranque con ledger (archivo aún no existe): NO re-enviar lo de
    # hoy — pudo haber salido antes de que existiera el ledger. Se siembran
    # las entradas vencidas como sent para arrancar limpio desde mañana.
    first_run = not send_ledger.LEDGER_PATH.exists()
    now = datetime.now(EC_TZ)
    hoy = send_ledger.today_iso()
    for key, fn, due in _catchup_specs():
        try:
            if not due(now) or send_ledger.already_sent(key, hoy):
                continue
            if first_run:
                send_ledger.confirm(key, hoy, detail="seeded (primer arranque con ledger)")
                continue
            logger.warning("CATCH-UP: %s no salió hoy — ejecutando ahora", key)
            await fn()
        except Exception:
            logger.exception("Catch-up de %s falló", key)


# ===== Lease de instancia única (Fase 3, auditoría S3) =====
# Si el App Service escala a >1 instancia, solo la dueña del lease corre el
# scheduler — sin esto, cada instancia mandaría TODOS los correos y cards.
# El lease vive en /home (compartido entre instancias) vía safe_json.
INSTANCE_ID = os.environ.get("WEBSITE_INSTANCE_ID", "") or f"local-{os.getpid()}"
_LEASE_PATH = REFS_PATH.parent / "scheduler_lease.json"
LEASE_TTL_SECONDS = 180
LEASE_REFRESH_SECONDS = 60


def _try_acquire_scheduler_lease() -> bool:
    granted = {"ok": False}

    def mutate(data: dict) -> None:
        now = datetime.now(EC_TZ)
        holder = data.get("holder")
        stale = True
        try:
            hb = datetime.fromisoformat(data.get("heartbeat", ""))
            stale = (now - hb).total_seconds() > LEASE_TTL_SECONDS
        except (ValueError, TypeError):
            stale = True
        if holder in (None, "", INSTANCE_ID) or stale:
            data["holder"] = INSTANCE_ID
            data["heartbeat"] = now.isoformat(timespec="seconds")
            granted["ok"] = True

    safe_json.locked_update(_LEASE_PATH, dict, mutate)
    return granted["ok"]


async def _scheduler_lease_loop() -> None:
    """Adquiere/renueva el lease; arranca el scheduler solo si es el dueño."""
    started = False
    while True:
        try:
            ok = await asyncio.to_thread(_try_acquire_scheduler_lease)
            if ok and not started:
                _schedule_jobs()
                scheduler.start()
                started = True
                logger.info("Scheduler started (lease %s). Next runs:", INSTANCE_ID)
                for job in scheduler.get_jobs():
                    logger.info("  %s → %s", job.id, job.next_run_time)
                await _catchup_missed_sends()
            elif not ok and started:
                logger.error("Lease del scheduler PERDIDO — apagando para no duplicar")
                scheduler.shutdown(wait=False)
                started = False
            elif not ok:
                logger.info("Otra instancia tiene el lease del scheduler — standby")
        except Exception:
            logger.exception("Error en el loop del lease del scheduler")
        await asyncio.sleep(LEASE_REFRESH_SECONDS)


# ===== FastAPI app =====
app = FastAPI(title="Biodegradables Bots — Data + Activities")


@app.on_event("startup")
async def _startup() -> None:
    # Fase 3: alerta por correo cuando un state file entra en cuarentena
    # (la corrupción ya no es silenciosa en ningún nivel).
    def _corruption_alert(path: Any, reason: str) -> None:
        _send_job_failure_alert(
            f"STATE CORRUPTO: {Path(str(path)).name}",
            f"El archivo fue puesto en cuarentena y se restauró el backup "
            f"si existía. Motivo: {reason}",
            1,
        )
    safe_json.on_corruption = _corruption_alert

    # El scheduler arranca vía el lease loop (instancia única).
    asyncio.create_task(_scheduler_lease_loop())

    # Warmup del cache de forecasting (Phase H/I) — evita timeout en primera
    # query de proyección. Corre en background, no bloquea startup.
    async def _warmup_forecast() -> None:
        try:
            import forecasting
            logger.info("Warming up forecasting cache (6 meses)...")
            await asyncio.to_thread(forecasting.historical_monthly_sales, 6)
            logger.info("Forecast cache warmed up")
        except Exception as e:
            logger.exception("Forecast warmup failed: %s", e)

    asyncio.create_task(_warmup_forecast())


@app.on_event("shutdown")
async def _shutdown() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)


@app.get("/")
async def root() -> dict[str, Any]:
    refs = _load_refs()
    return {
        "service": "biodegradables-bots",
        "status": "ok",
        "endpoints": {
            "data": "/api/messages",
            "activities": "/api/activities/messages",
        },
        "scheduler_jobs": [
            {"id": j.id, "next_run": str(j.next_run_time)}
            for j in scheduler.get_jobs()
        ] if scheduler.running else [],
        "registered_users": {
            "data": list(refs.get("data", {}).keys()),
            "activities": list(refs.get("activities", {}).keys()),
        },
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    # Fase 3: el health expone qué reportes salieron hoy (ledger) y quién
    # tiene el scheduler — observabilidad de entregas sin entrar a logs.
    lease = safe_json.load_json(_LEASE_PATH, dict)
    return {
        "status": "healthy",
        "scheduler_running": scheduler.running,
        "scheduler_lease": {
            "holder": lease.get("holder"),
            "heartbeat": lease.get("heartbeat"),
            "this_instance": INSTANCE_ID,
        },
        "sends_today": send_ledger.status_today(),
    }


@app.post("/admin/trigger-checkin")
async def trigger_checkin(request: Request) -> dict[str, Any]:
    _require_admin(request)
    await send_daily_checkin()
    refs = _load_refs()
    return {
        "status": "triggered",
        "users": list(refs.get("activities", {}).keys()),
    }


@app.post("/admin/trigger-reminders")
async def trigger_reminders(request: Request) -> dict[str, Any]:
    """Forzar entrega de reminders vencidos ahora (testing)."""
    _require_admin(request)
    await deliver_due_reminders()
    return {
        "status": "triggered",
        "pending_count": len(reminders.list_reminders(only_pending=True)),
    }


@app.post("/admin/trigger-cobranzas")
async def trigger_cobranzas(request: Request) -> dict[str, Any]:
    """Forzar auto-asignación de cobranzas ahora (testing)."""
    _require_admin(request)
    await auto_assign_cobranzas()
    return {"status": "triggered"}


@app.post("/admin/trigger-weekly-summaries")
async def trigger_weekly_summaries(request: Request) -> dict[str, Any]:
    """Forzar envío de weekly summaries ahora (testing)."""
    _require_admin(request)
    await send_weekly_summaries()
    return {"status": "triggered"}


@app.post("/admin/trigger-news-brief")
async def trigger_news_brief(request: Request) -> dict[str, Any]:
    """Forzar generación del news brief ahora (testing)."""
    _require_admin(request)
    await generate_daily_news_brief()
    brief = news_brief.load_brief()
    return {
        "status": "triggered",
        "fresh": news_brief.is_brief_fresh(),
        "generated_at": brief.get("generated_at"),
    }


@app.post("/admin/trigger-sales-recap")
async def trigger_sales_recap(request: Request) -> dict[str, Any]:
    """Forzar sales recap. Body opcional: {year, month}. Default: mes anterior."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    year = body.get("year")
    month = body.get("month")
    result = await asyncio.to_thread(monthly_recap.send_sales_recap, year, month)
    return result


@app.post("/admin/trigger-activities-recap")
async def trigger_activities_recap(request: Request) -> dict[str, Any]:
    """Forzar activities recap."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    year = body.get("year")
    month = body.get("month")
    result = await asyncio.to_thread(monthly_recap.send_activities_recap, year, month)
    return result


@app.post("/admin/trigger-midmonth-status")
async def trigger_midmonth_status(request: Request) -> dict[str, Any]:
    """Forzar midmonth status."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    year = body.get("year")
    month = body.get("month")
    result = await asyncio.to_thread(monthly_recap.send_midmonth_status, year, month)
    return result


@app.post("/admin/seed-template-for-user")
async def seed_template_for_user(request: Request) -> dict[str, Any]:
    """Sincroniza el template de un usuario con su semana ACTUAL.

    Lee `activities_template_<slug>.json`, compara contra `user.weeks[wk].activities`
    y agrega las faltantes via `add_adhoc`. Idempotente: no duplica ni borra.
    Útil cuando se actualiza el template (con actividades nuevas) y se quiere que
    aparezcan en la semana en curso sin esperar al lunes próximo.

    Body JSON: {"user_email": "gsanchez@biodegradablesecuador.com"}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    if not user_email or "@" not in user_email:
        raise HTTPException(status_code=400, detail="user_email requerido")

    template = activity_state.load_template(user_email)
    template_activities = template.get("activities", [])
    if not template_activities:
        return {
            "user": user_email,
            "status": "template_empty",
            "added": [],
            "already_present": [],
        }

    week = activity_state.get_week(user_email)
    existing_ids = set(week["activities"].keys())

    added: list[dict[str, Any]] = []
    already: list[str] = []
    for a in template_activities:
        aid = a["id"]
        if aid in existing_ids:
            already.append(aid)
            continue
        try:
            activity_state.add_adhoc(
                aid,
                a["nombre"],
                user_email=user_email,
                tipo=a.get("tipo", "semanal"),
                meta=a.get("meta"),
                unidad=a.get("unidad", ""),
                fuente=a.get("fuente", "manual"),
            )
            added.append({
                "id": aid,
                "nombre": a["nombre"],
                "tipo": a.get("tipo", "semanal"),
                "meta": a.get("meta"),
                "unidad": a.get("unidad", ""),
            })
        except Exception as e:
            logger.exception("Falló add_adhoc para %s/%s: %s", user_email, aid, e)
            added.append({"id": aid, "error": str(e)})

    return {
        "user": user_email,
        "wk": activity_state.week_key(),
        "status": "synced",
        "added_count": len([x for x in added if "error" not in x]),
        "added": added,
        "already_present": already,
    }


@app.get("/admin/show-activities-for-user")
async def show_activities_for_user(request: Request) -> dict[str, Any]:
    """Devuelve las actividades de la semana ACTUAL de un user (o de todos si
    no se pasa user_email). Para debugging — ver qué se le creó/quedó.

    Query: ?user_email=foo@bar.com (opcional)
    """
    _require_admin(request)

    target_email = request.query_params.get("user_email", "").strip().lower()
    state = activity_state.load()
    out: dict[str, Any] = {"wk": activity_state.week_key(), "users": {}}

    for email, user_data in state.get("users", {}).items():
        if target_email and email != target_email:
            continue
        weeks = user_data.get("weeks", {})
        wk_data = weeks.get(activity_state.week_key()) or weeks.get(
            sorted(weeks.keys())[-1] if weeks else "", {}
        )
        if not wk_data:
            out["users"][email] = {"warning": "no weeks"}
            continue
        activities = wk_data.get("activities", {})
        out["users"][email] = {
            "activities_count": len(activities),
            "activities": [
                {
                    "id": aid,
                    "nombre": a.get("nombre"),
                    "tipo": a.get("tipo"),
                    "meta": a.get("meta"),
                    "unidad": a.get("unidad"),
                    "priority": a.get("priority", "(sin marcar)"),
                    "adhoc": a.get("adhoc", False),
                }
                for aid, a in activities.items()
            ],
        }
    return out


@app.post("/admin/preview-checkin-as-user")
async def preview_checkin_as_user(request: Request) -> dict[str, Any]:
    """Construye el check-in card como si fueras `as_email` y lo manda al
    ref de `send_to_email` (default: malvarado@). Útil para que Mateo vea
    el card que recibiría otro colaborador (ej. info@ con su sub-card del
    cierre de caja) ANTES de que el bot lo mande automático.

    Body: {"as_email": "info@...", "send_to_email": "malvarado@..." }
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    as_email = (body or {}).get("as_email", "").strip().lower()
    send_to_email = (body or {}).get(
        "send_to_email", "malvarado@biodegradablesecuador.com"
    ).strip().lower()
    if not as_email or "@" not in as_email:
        raise HTTPException(status_code=400, detail="as_email requerido")

    refs = _load_refs()
    target_ref_dict = refs.get("activities", {}).get(send_to_email)
    if not target_ref_dict:
        raise HTTPException(
            status_code=400,
            detail=f"{send_to_email} no tiene ref del Activities Bot",
        )

    ref = ConversationReference().deserialize(target_ref_dict)

    async def cb(turn_context: TurnContext, _as: str = as_email) -> None:
        sucursal = SUCURSAL_POR_USER.get(_as, "")
        marker = (
            f"📋 **PREVIEW** — esto es lo que recibirá `{_as}` "
            f"({sucursal or 'sucursal n/d'}) hoy a las 5:15 PM:"
        )
        await turn_context.send_activity(marker)
        await turn_context.send_activity(_build_checkin_card(_as))
        await turn_context.send_activity(
            "_(Es solo preview — si lo llenás vos, las marcas quedan en TU "
            f"state, no en el de `{_as}`.)_"
        )

    await activities_adapter.continue_conversation(
        ref, cb, bot_id=ACTIVITIES_APP_ID
    )
    return {"status": "sent", "as": as_email, "to": send_to_email}


@app.get("/admin/aad-lookup")
async def admin_aad_lookup_get(request: Request) -> dict[str, Any]:
    """Phase V (2026-06-11): muestra el lookup AAD ID → email aprendido +
    overrides activos. Útil para auditar quién está mapeado a quién."""
    _require_admin(request)
    return {
        "learned_lookup": _load_aad_lookup(),
        "env_overrides": AAD_OVERRIDE,
        "lookup_path": str(_AAD_LOOKUP_PATH),
    }


@app.post("/admin/aad-lookup/set")
async def admin_aad_lookup_set(request: Request) -> dict[str, Any]:
    """Phase V (2026-06-11): fuerza un mapeo AAD ID → email manualmente.

    Body: {"aad_short": "435a855e", "email": "jsolorzano@..."}
    Sobrescribe si ya existía.
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    aad_short = (body.get("aad_short") or "").strip().lower()
    email = (body.get("email") or "").strip().lower()
    if not aad_short or "@" not in email:
        raise HTTPException(status_code=400, detail="aad_short y email son requeridos")
    lookup = _load_aad_lookup()
    old = lookup.get(aad_short)
    lookup[aad_short] = email
    _save_aad_lookup(lookup)
    return {"ok": True, "aad_short": aad_short, "email": email, "previous": old}


@app.post("/admin/aad-lookup/remove")
async def admin_aad_lookup_remove(request: Request) -> dict[str, Any]:
    """Phase V (2026-06-11): borra un mapeo aprendido (ej. si quedó mal).

    Body: {"aad_short": "435a855e"}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    aad_short = (body.get("aad_short") or "").strip().lower()
    if not aad_short:
        raise HTTPException(status_code=400, detail="aad_short requerido")
    lookup = _load_aad_lookup()
    removed = lookup.pop(aad_short, None)
    _save_aad_lookup(lookup)
    return {"ok": True, "aad_short": aad_short, "removed": removed}


@app.post("/admin/trigger-morning-sales-job")
async def trigger_morning_sales_job_admin(request: Request) -> dict[str, Any]:
    """Phase V (2026-06-11): dispara `send_morning_sales_report_job` ahora
    mismo, sin esperar al cron. Útil para test post-fix."""
    _require_admin(request)
    try:
        await send_morning_sales_report_job()
        return {"ok": True, "msg": "morning_sales_report disparado"}
    except Exception as e:
        logger.exception("trigger morning_sales failed: %s", e)
        return {"ok": False, "error": str(e)}


@app.post("/admin/preview-jose-route")
async def preview_jose_route(request: Request) -> dict[str, Any]:
    """Phase U: dispara el card de ruta de José al ref de `send_to_email`
    (default: malvarado@), para que Mateo lo previsualice.

    Body: {"send_to_email": "malvarado@..."}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    send_to_email = (body or {}).get(
        "send_to_email", "malvarado@biodegradablesecuador.com"
    ).strip().lower()

    refs = _load_refs()
    target_ref = refs.get("activities", {}).get(send_to_email)
    if not target_ref:
        raise HTTPException(
            status_code=400,
            detail=f"{send_to_email} no tiene ref del Activities Bot",
        )
    ref = ConversationReference().deserialize(target_ref)

    async def cb(turn_context: TurnContext) -> None:
        await turn_context.send_activity(
            f"📋 **PREVIEW** — esto es lo que recibirá José hoy a las 11 AM y 3 PM:"
        )
        await turn_context.send_activity(_build_jose_ruta_card(JOSE_EMAIL))
        await turn_context.send_activity(
            "_(Preview — si apretás botones, las marcas quedan en el state de JOSÉ.)_"
        )

    await activities_adapter.continue_conversation(
        ref, cb, bot_id=ACTIVITIES_APP_ID
    )
    return {"status": "sent", "to": send_to_email}


@app.post("/admin/preview-jose-summary-email")
async def preview_jose_summary_email(request: Request) -> dict[str, Any]:
    """Phase U: manda el email resumen del día de José al `to_override`
    (por defecto solo a Mateo) para preview ANTES del envío real de las 18:30.

    Body: {"to_override": "malvarado@..."}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    to = (body or {}).get(
        "to_override", "malvarado@biodegradablesecuador.com"
    ).strip().lower()
    try:
        html_body = _jose_summary_html(activity_state._today().isoformat())
        from graph_mail import send as graph_send
        graph_send(
            from_user=JOSE_EMAIL,
            to=[to],
            subject=f"[PREVIEW] 🚚 Resumen del día — José — {activity_state._today().strftime('%d/%m/%Y')}",
            html_body=html_body,
        )
        return {"status": "sent", "to": to}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/admin/send-message-to-users")
async def send_message_to_users(request: Request) -> dict[str, Any]:
    """Manda un mensaje de texto plano a uno o varios users via Activities Bot.

    Body: {users: [email...], message: "texto"}
    Si users no se pasa, manda a CIERRE_CAJA_USERS (info@ + quito@).

    Phase S+ (2026-06-08).
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    targets = body.get("users") or list(CIERRE_CAJA_USERS)
    targets = [t.strip().lower() for t in targets if t]
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message es requerido")

    refs = _load_refs()
    activities_refs = refs.get("activities", {})
    sent: list[str] = []
    failed: list[str] = []
    for email in targets:
        ref_dict = activities_refs.get(email)
        if not ref_dict:
            failed.append(f"{email} (sin ref)")
            continue
        try:
            ref = ConversationReference().deserialize(ref_dict)

            async def cb(turn_context: TurnContext, _msg: str = message) -> None:
                await turn_context.send_activity(_msg)

            await activities_adapter.continue_conversation(
                ref, cb, bot_id=ACTIVITIES_APP_ID
            )
            sent.append(email)
        except Exception as e:
            failed.append(f"{email} ({e})")
    return {"sent": sent, "failed": failed}


@app.post("/admin/schedule-one-time-message")
async def schedule_one_time_message(request: Request) -> dict[str, Any]:
    """Programa un mensaje proactivo para envío a futuro via APScheduler.

    Body: {
      users: [emails],
      message: "...",
      send_at_iso: "2026-06-08T16:00:00-05:00",
      job_id: "aviso_sucursales_$timestamp" (optional)
    }
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    targets = [t.strip().lower() for t in (body.get("users") or []) if t]
    message = (body.get("message") or "").strip()
    send_at_iso = body.get("send_at_iso", "").strip()
    job_id = body.get("job_id") or f"one_time_msg_{send_at_iso}"
    if not targets or not message or not send_at_iso:
        raise HTTPException(
            status_code=400,
            detail="users, message y send_at_iso son requeridos",
        )

    try:
        send_at = datetime.fromisoformat(send_at_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="send_at_iso inválido")

    from apscheduler.triggers.date import DateTrigger

    async def _deliver():
        refs = _load_refs()
        activities_refs = refs.get("activities", {})
        for email in targets:
            ref_dict = activities_refs.get(email)
            if not ref_dict:
                logger.warning("scheduled msg: %s sin ref", email)
                continue
            try:
                ref = ConversationReference().deserialize(ref_dict)

                async def cb(turn_context: TurnContext, _msg: str = message) -> None:
                    await turn_context.send_activity(_msg)

                await activities_adapter.continue_conversation(
                    ref, cb, bot_id=ACTIVITIES_APP_ID
                )
                logger.info("scheduled msg enviado a %s", email)
            except Exception as e:
                logger.exception("scheduled msg falló a %s: %s", email, e)

    scheduler.add_job(
        _deliver,
        DateTrigger(run_date=send_at),
        id=job_id,
        replace_existing=True,
    )
    return {
        "scheduled": True,
        "job_id": job_id,
        "send_at": send_at.isoformat(),
        "targets": targets,
    }


@app.post("/admin/schedule-one-time-email")
async def schedule_one_time_email(request: Request) -> dict[str, Any]:
    """Phase S+: programa un envío de email para futuro via APScheduler.

    Body: {
      from_user: "malvarado@...",
      to: [emails],
      cc: [emails] (optional),
      subject: "...",
      html_body: "...",
      send_at_iso: "2026-06-08T16:00:00-05:00",
      job_id: "..." (optional)
    }
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    from_user = (body.get("from_user") or "").strip()
    to_list = [e.strip() for e in (body.get("to") or []) if e.strip()]
    cc_list = [e.strip() for e in (body.get("cc") or []) if e.strip()]
    subject = (body.get("subject") or "").strip()
    html_body = body.get("html_body") or ""
    send_at_iso = (body.get("send_at_iso") or "").strip()
    job_id = body.get("job_id") or f"email_one_time_{send_at_iso}"

    if not from_user or not to_list or not subject or not html_body or not send_at_iso:
        raise HTTPException(
            status_code=400,
            detail="from_user, to, subject, html_body y send_at_iso son requeridos",
        )

    try:
        send_at = datetime.fromisoformat(send_at_iso)
    except ValueError:
        raise HTTPException(status_code=400, detail="send_at_iso inválido")

    from apscheduler.triggers.date import DateTrigger
    import graph_mail as _gm

    def _deliver() -> None:
        try:
            _gm.send(
                from_user=from_user,
                to=to_list,
                cc=cc_list or None,
                subject=subject,
                html_body=html_body,
            )
            logger.info(
                "scheduled email enviado a %s (cc=%s) subject=%s",
                to_list, cc_list, subject,
            )
        except Exception as e:
            logger.exception("scheduled email falló: %s", e)

    scheduler.add_job(
        _deliver,
        DateTrigger(run_date=send_at),
        id=job_id,
        replace_existing=True,
    )
    return {
        "scheduled": True,
        "job_id": job_id,
        "send_at": send_at.isoformat(),
        "to": to_list,
        "cc": cc_list,
        "subject": subject,
    }


@app.post("/admin/preview-apertura-caja")
async def preview_apertura_caja(request: Request) -> dict[str, Any]:
    """Phase S: preview del card matinal de apertura de caja.

    Body: {as_email (info@ o quito@), send_to_email (default malvarado@)}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    as_email = (body or {}).get("as_email", "info@biodegradablesecuador.com").strip().lower()
    send_to = (body or {}).get(
        "send_to_email", "malvarado@biodegradablesecuador.com"
    ).strip().lower()

    refs = _load_refs()
    target_ref_dict = refs.get("activities", {}).get(send_to)
    if not target_ref_dict:
        raise HTTPException(status_code=400, detail=f"{send_to} no tiene ref")

    ref = ConversationReference().deserialize(target_ref_dict)

    async def cb(turn_context: TurnContext, _as: str = as_email) -> None:
        sucursal = SUCURSAL_POR_USER.get(_as, "")
        await turn_context.send_activity(
            f"☀️ **PREVIEW** — Card matinal 8:15 AM que recibirá `{_as}` ({sucursal}):"
        )
        await turn_context.send_activity(_build_apertura_caja_card(_as))

    await activities_adapter.continue_conversation(
        ref, cb, bot_id=ACTIVITIES_APP_ID
    )
    return {"status": "sent", "as": as_email, "to": send_to}


@app.post("/admin/preview-confirmacion-cierre")
async def preview_confirmacion_cierre(request: Request) -> dict[str, Any]:
    """Phase P: preview del card de confirmación de cierre que llega al validador.

    Body: {emisor_email, fecha, send_to_email (override del validador real)}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    emisor = (body or {}).get("emisor_email", "info@biodegradablesecuador.com").strip().lower()
    fecha = (body or {}).get("fecha", activity_state._today().isoformat()).strip()
    send_to = (body or {}).get("send_to_email", "malvarado@biodegradablesecuador.com").strip().lower()

    cierre = activity_state.get_cierre_caja(emisor, fecha)
    if not cierre:
        # Crear un cierre sample para que el preview tenga datos
        sample_denoms = {
            "b100": 1, "b50": 2, "b20": 4, "b10": 3, "b5": 5, "b1": 7,
            "m1": 6, "m050": 8, "m025": 12, "m010": 25, "m005": 14, "m001": 33,
        }
        suc_sample = SUCURSAL_POR_USER.get(emisor, "Guayaquil")
        activity_state.set_cierre_caja(
            emisor, fecha, sample_denoms,
            notas="(preview con datos sample)", sucursal=suc_sample, realizado=True,
        )
        cierre = activity_state.get_cierre_caja(emisor, fecha)

    sucursal = cierre.get("sucursal") or SUCURSAL_POR_USER.get(emisor, "")
    refs = _load_refs()
    target_ref_dict = refs.get("activities", {}).get(send_to)
    if not target_ref_dict:
        raise HTTPException(
            status_code=400,
            detail=f"{send_to} no tiene ref del Activities Bot",
        )

    ref = ConversationReference().deserialize(target_ref_dict)
    card = _build_confirmacion_cierre_card(
        emisor_email=emisor, fecha=fecha, sucursal=sucursal,
        total=cierre["total"], entregado=cierre["entregado"],
        fondo=cierre["fondo"], es_recordatorio=False,
    )

    async def cb(turn_context: TurnContext, _card: Activity = card) -> None:
        await turn_context.send_activity(
            "📋 **PREVIEW** — este es el card que recibirá Daniel/Gabriela cuando "
            f"`{emisor}` termine su cierre:"
        )
        await turn_context.send_activity(_card)

    await activities_adapter.continue_conversation(
        ref, cb, bot_id=ACTIVITIES_APP_ID
    )
    return {"status": "sent", "emisor": emisor, "to": send_to}


@app.post("/admin/trigger-consolidated-daily-summary")
async def trigger_consolidated_daily(request: Request) -> dict[str, Any]:
    """Dispara el consolidated daily summary ahora (para testing).

    Body opcional: {"to_override": ["..."], "cc_override": ["..."]} — si se pasan,
    setean las env vars temporalmente para esta corrida solo.
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    to_override = (body or {}).get("to_override")
    cc_override = (body or {}).get("cc_override")

    # Fase 2 (auditoría A8): los overrides van como parámetros — ya no se
    # muta os.environ (el job de las 18:30 heredaba los destinatarios del
    # último test hasta el siguiente restart).
    from ask_agent import _send_consolidated_daily_summary
    result = await asyncio.to_thread(
        _send_consolidated_daily_summary, to_override, cc_override
    )
    return result


@app.post("/admin/reset-day-for-user")
async def reset_day_for_user(request: Request) -> dict[str, Any]:
    """Resetea las marcas de un día específico de un user (testing).

    Borra: cierre de caja + day_schedule + log de cada activity + entregas de chocolates.
    NO toca activities asignadas ni stock_inicial de chocolates.

    Body: {"user_email": "info@...", "fecha": "2026-06-05" (default: hoy)}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    fecha = (body or {}).get("fecha") or activity_state._today().isoformat()
    if not user_email or "@" not in user_email:
        raise HTTPException(status_code=400, detail="user_email requerido")

    result = activity_state.reset_day(user_email, fecha)
    return result


@app.post("/admin/wipe-user-from-activities")
async def wipe_user_from_activities(request: Request) -> dict[str, Any]:
    """Borra TODO el state de un user del Activities Bot + su ref proactivo.

    NO toca el ref del Data Bot (el usuario sigue pudiendo usar ese para queries).
    Usado para limpiar supervisores que se metieron al Activities Bot por error.

    Body: {"user_email": "dsanchez@biodegradablesecuador.com"}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    if not user_email or "@" not in user_email:
        raise HTTPException(status_code=400, detail="user_email requerido")

    # 1) Borrar state (weeks + cierres_caja + day_schedules)
    state_wiped = activity_state.wipe_user(user_email)

    # 2) Borrar el ref del Activities Bot (no toca Data Bot).
    # Bajo lock: un mensaje entrante simultáneo ya no puede resucitar el ref
    # recién borrado ni perder el de otro user (auditoría H12).
    ref_removed = False
    with _REFS_LOCK:
        refs = _load_refs()
        activities_refs = refs.get("activities", {})
        if user_email in activities_refs:
            del activities_refs[user_email]
            refs["activities"] = activities_refs
            _save_refs(refs)
            ref_removed = True

    return {
        "user": user_email,
        "state_wiped": state_wiped,
        "activities_ref_removed": ref_removed,
    }


@app.post("/admin/add-activity-for-user")
async def add_activity_for_user(request: Request) -> dict[str, Any]:
    """Agrega una actividad ad-hoc a la semana actual de un user.

    Body:
      {
        "user_email": "info@...",
        "activity_id": "cobranza-acme-2026-06-05",
        "nombre": "📞 Cobranza: ACME SA — $1234 (45d)",
        "tipo": "diaria",       (optional, default semanal)
        "meta": 1,              (optional)
        "unidad": "cliente",    (optional)
        "priority": "alta"      (optional)
      }
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    activity_id = (body or {}).get("activity_id", "").strip()
    nombre = (body or {}).get("nombre", "").strip()
    if not user_email or not activity_id or not nombre:
        raise HTTPException(
            status_code=400,
            detail="user_email, activity_id y nombre son requeridos",
        )

    try:
        entry = activity_state.add_adhoc(
            activity_id,
            nombre,
            user_email=user_email,
            tipo=body.get("tipo", "semanal"),
            meta=body.get("meta"),
            unidad=body.get("unidad", ""),
            fuente=body.get("fuente", "manual"),
        )
        priority = body.get("priority")
        if priority:
            activity_state.set_priority(activity_id, priority, user_email=user_email)
        return {"ok": True, "user": user_email, "activity": entry}
    except ValueError as e:
        # Ej. ya existe — lo tratamos como warning
        return {"ok": False, "user": user_email, "activity_id": activity_id, "error": str(e)}


@app.post("/admin/remove-activity-for-user")
async def remove_activity_for_user(request: Request) -> dict[str, Any]:
    """Borra una activity puntual de la semana ACTUAL de un user.

    Body: {"user_email": "...", "activity_id": "..."}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    activity_id = (body or {}).get("activity_id", "").strip()
    if not user_email or not activity_id:
        raise HTTPException(status_code=400, detail="user_email y activity_id requeridos")

    removed = activity_state.remove_activity(activity_id, user_email=user_email)
    return {
        "user": user_email,
        "activity_id": activity_id,
        "removed": removed,
    }


@app.post("/admin/set-priorities-for-user")
async def set_priorities_for_user(request: Request) -> dict[str, Any]:
    """Marca prioridades (alta/media/baja) de varias actividades en batch.

    Body JSON:
      {
        "user_email": "gsanchez@biodegradablesecuador.com",
        "priorities": {
          "scrum-diaria": "alta",
          "pagos-proveedores-quincena": "alta",
          ...
        }
      }
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    user_email = (body or {}).get("user_email", "").strip().lower()
    priorities = (body or {}).get("priorities") or {}
    if not user_email or "@" not in user_email:
        raise HTTPException(status_code=400, detail="user_email requerido")
    if not isinstance(priorities, dict) or not priorities:
        raise HTTPException(status_code=400, detail="priorities (dict) requerido")

    set_results: list[dict[str, Any]] = []
    for aid, prio in priorities.items():
        try:
            activity_state.set_priority(aid, prio, user_email=user_email)
            set_results.append({"id": aid, "priority": prio, "ok": True})
        except Exception as e:
            set_results.append({"id": aid, "priority": prio, "ok": False, "error": str(e)})

    return {
        "user": user_email,
        "wk": activity_state.week_key(),
        "results": set_results,
        "ok_count": sum(1 for r in set_results if r["ok"]),
        "fail_count": sum(1 for r in set_results if not r["ok"]),
    }


@app.get("/admin/state-debug")
async def state_debug(request: Request) -> dict[str, Any]:
    """Debug: muestra los paths reales donde el bot escribe state, si los
    archivos existen, y un snippet. Para diagnosticar persistence."""
    _require_admin(request)

    import os as _os
    paths_to_check = {
        "Path.home()": str(Path.home()),
        "HOME env": _os.environ.get("HOME", "(unset)"),
        "STATE_DIR env": _os.environ.get("STATE_DIR", "(unset)"),
        "refs_path": str(REFS_PATH),
        "refs_exists": REFS_PATH.exists(),
        "refs_size_bytes": REFS_PATH.stat().st_size if REFS_PATH.exists() else None,
    }
    # Lista archivos en el dir
    try:
        files_in_dir = [
            {"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
            for p in REFS_PATH.parent.glob("*")
            if p.is_file()
        ]
    except Exception as e:
        files_in_dir = [{"error": str(e)}]
    paths_to_check["files_in_state_dir"] = files_in_dir
    return paths_to_check


@app.post("/api/messages")
async def data_messages(request: Request) -> Response:
    """Endpoint del Data Bot (gerencia)."""
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(status_code=415, detail="application/json required")
    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("authorization", "")
    try:
        await data_adapter.process_activity(activity, auth_header, _on_turn_data)
        return Response(status_code=200)
    except Exception as e:
        logger.exception("Data adapter error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/activities/messages")
async def activities_messages(request: Request) -> Response:
    """Endpoint del Activities Bot (colaboradores)."""
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(status_code=415, detail="application/json required")
    body = await request.json()
    activity = Activity().deserialize(body)
    auth_header = request.headers.get("authorization", "")
    try:
        await activities_adapter.process_activity(
            activity, auth_header, _on_turn_activities
        )
        return Response(status_code=200)
    except Exception as e:
        logger.exception("Activities adapter error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 3978))
    logger.info("Iniciando teams_bot en puerto %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
