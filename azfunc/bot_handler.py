"""Handler del bot de Teams para Azure Functions.

Recibe activities de Bot Framework via HTTP trigger y las procesa con el
adapter clásico (BotFrameworkAdapter, App ID + secret).

Comandos soportados:
- /help              — muestra ayuda
- /mark <factura> <ok|no|parcial> [razón]  — confirma despacho
- /status            — resumen de despachos pendientes (TODO)

Además guarda la conversation reference de cada usuario que escribe al bot
(en Azure Tables), para poder enviarle Adaptive Cards proactivamente desde
otros jobs (logistics_morning).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from botbuilder.core import (
    BotFrameworkAdapter,
    BotFrameworkAdapterSettings,
    TurnContext,
)
from botbuilder.schema import Activity, ActivityTypes, ConversationReference

import dispatch_state

logger = logging.getLogger("bot_handler")

APP_ID = os.environ.get("MICROSOFT_APP_ID", "")
APP_PASSWORD = os.environ.get("MICROSOFT_APP_PASSWORD", "")
APP_TENANT_ID = os.environ.get("MICROSOFT_APP_TENANT_ID", "")

# Whitelist: dos niveles de acceso
# - CONVERSATIONAL: pueden hacer chat libre con Claude sobre datos Contifico
# - DISPATCH_ONLY: solo pueden usar /mark para confirmar despachos
ALLOWED_CONVERSATIONAL = {
    e.strip().lower()
    for e in os.environ.get(
        "BOT_ALLOWED_CONVERSATIONAL",
        "dsanchez@biodegradablesecuador.com,"
        "gsanchez@biodegradablesecuador.com,"
        "malvarado@biodegradablesecuador.com",
    ).split(",")
    if e.strip()
}
ALLOWED_DISPATCH = {
    e.strip().lower()
    for e in os.environ.get(
        "BOT_ALLOWED_DISPATCH",
        "dsanchez@biodegradablesecuador.com,"
        "gsanchez@biodegradablesecuador.com,"
        "malvarado@biodegradablesecuador.com,"
        "quito@biodegradablesecuador.com,"
        "guayaquil@biodegradablesecuador.com",
    ).split(",")
    if e.strip()
}

_settings_kwargs: dict[str, Any] = {"app_id": APP_ID, "app_password": APP_PASSWORD}
if APP_TENANT_ID:
    _settings_kwargs["channel_auth_tenant"] = APP_TENANT_ID

settings = BotFrameworkAdapterSettings(**_settings_kwargs)
adapter = BotFrameworkAdapter(settings)


async def _on_error(context: TurnContext, error: Exception) -> None:
    logger.exception("Adapter error: %s", error)
    try:
        await context.send_activity(f"⚠️ Error interno: {error}")
    except Exception:
        pass


adapter.on_turn_error = _on_error


WELCOME_CHAT = (
    "👋 ¡Hola! Soy el asistente de Biodegradables Ecuador.\n\n"
    "Podés preguntarme cosas como:\n"
    "• ¿Cuánto vendimos hoy?\n"
    "• ¿Cuánto facturamos en mayo?\n"
    "• ¿Quién es el cliente que más compró este mes?\n"
    "• ¿Qué pedidos están pendientes de despachar?\n"
    "• Buscame las facturas de MULANO de los últimos 60 días\n\n"
    "**Comandos directos:**\n"
    "• `/mark <factura> <ok|no|parcial> [razón]` — confirma despacho\n"
    "• `/help` — esta ayuda\n"
)

WELCOME_DISPATCH = (
    "👋 ¡Hola! Soy el bot de despachos de Biodegradables Ecuador.\n\n"
    "**Tu rol:** confirmar el estado de los pedidos que se envían desde tu bodega.\n\n"
    "**Comandos:**\n"
    "• `/mark <factura> <ok|no|parcial> [razón]` — confirma despacho\n"
    "  Ejemplos:\n"
    "  - `/mark 001-002-000008181 ok`\n"
    "  - `/mark 001-001-000012567 no Cliente cambió dirección`\n"
    "  - `/mark 001-002-000008180 parcial Falta 1 caja`\n"
    "• `/help` — esta ayuda\n"
)

MARK_RE = re.compile(
    r"^/mark\s+(\S+)\s+(ok|no|parcial)\s*(.*)$", re.IGNORECASE
)


def _user_email(activity: Activity) -> str:
    """Intenta extraer el email del usuario de Teams."""
    try:
        from_prop = activity.from_property
        if from_prop:
            channel_data = activity.channel_data or {}
            if isinstance(channel_data, dict):
                email = channel_data.get("email")
                if email:
                    return email.lower()
            props = getattr(from_prop, "additional_properties", None) or {}
            if isinstance(props, dict):
                email = props.get("email") or props.get("upn") or ""
                if email:
                    return email.lower()
            name = getattr(from_prop, "name", "") or ""
            return name.lower()
    except Exception as e:
        logger.warning("No pude extraer email: %s", e)
    return ""


def _is_conversational(email: str) -> bool:
    return email.lower() in ALLOWED_CONVERSATIONAL


def _is_dispatch(email: str) -> bool:
    return email.lower() in ALLOWED_DISPATCH


def _identifier_from_email(email: str) -> str:
    """Mapea email a un identificador corto para dispatch_state.marcado_por."""
    if "quito@" in email:
        return "jefe_uio"
    if "guayaquil@" in email:
        return "jefe_gye"
    return f"teams:{email}"


# ----------- Conversation references (para envío proactivo) -----------
def _save_conversation_reference(activity: Activity, email: str) -> None:
    """Guarda la conversation reference en Azure Tables para envío proactivo."""
    if not email:
        return
    try:
        from azure.data.tables import TableServiceClient
        conn_str = os.environ.get("AzureWebJobsStorage", "")
        if not conn_str:
            return
        service = TableServiceClient.from_connection_string(conn_str)
        try:
            service.create_table_if_not_exists("botconvrefs")
        except Exception:
            pass
        client = service.get_table_client("botconvrefs")
        ref = TurnContext.get_conversation_reference(activity)
        client.upsert_entity({
            "PartitionKey": "ref",
            "RowKey": email.lower().replace("/", "_").replace("\\", "_"),
            "reference_json": json.dumps(ref.serialize() if hasattr(ref, "serialize") else ref.__dict__, default=str),
        })
    except Exception as e:
        logger.warning("No pude guardar conversation reference: %s", e)


# ----------- Lógica principal -----------
async def _on_turn(context: TurnContext) -> None:
    activity = context.activity

    if activity.type == ActivityTypes.conversation_update:
        if activity.members_added:
            for m in activity.members_added:
                if m.id != activity.recipient.id:
                    # En el saludo inicial no sabemos rol todavía, mostrar el chat
                    await context.send_activity(WELCOME_CHAT)
        return

    if activity.type != ActivityTypes.message:
        return

    email = _user_email(activity)
    channel_id = activity.channel_id or ""

    # Log detallado para debug
    logger.info(
        "Activity recibida — channel=%s email='%s' name='%s' aad_id='%s'",
        channel_id,
        email,
        getattr(activity.from_property, "name", "") or "",
        getattr(activity.from_property, "aad_object_id", "") or "",
    )

    # Sin email identificable (Web Chat anónimo) → tratamos como dispatch para test.
    # En Teams real siempre debería venir el email.
    if not email:
        logger.info("Sin email — modo permisivo (probablemente Web Chat de prueba)")

    is_conv = _is_conversational(email) if email else True   # permisivo si no hay email
    is_disp = _is_dispatch(email) if email else True

    if email and not (is_conv or is_disp):
        logger.warning("No autorizado: %s", email)
        await context.send_activity(
            "Lo siento, no tienes acceso. Pídele a Mateo que te agregue al bot."
        )
        return

    _save_conversation_reference(activity, email)

    text = (activity.text or "").strip()
    if not text:
        return

    low = text.lower()

    # /help
    if low in ("/help", "help", "?"):
        welcome = WELCOME_CHAT if is_conv else WELCOME_DISPATCH
        await context.send_activity(welcome)
        return

    # /mark factura status [razon]
    m = MARK_RE.match(text)
    if m:
        if not is_disp:
            await context.send_activity("No tenés permiso para marcar despachos.")
            return
        factura, status, razon = m.group(1), m.group(2).upper(), m.group(3).strip()
        try:
            rec = dispatch_state.mark(
                factura, status, razon=razon, marcado_por=_identifier_from_email(email)
            )
            emoji = {"OK": "✅", "NO": "❌", "PARCIAL": "🕐"}.get(status, "")
            msg = f"{emoji} **{factura}** → {status}"
            if razon:
                msg += f"\n_Razón:_ {razon}"
            msg += f"\n_Marcado por {rec['marcado_por']} a las {rec['marcado_en']}_"
            await context.send_activity(msg)
        except Exception as e:
            await context.send_activity(f"⚠️ Error marcando despacho: {e}")
        return

    # Texto libre → chat con Claude (solo conversacionales)
    if not is_conv:
        await context.send_activity(
            "Solo puedo responder comandos de despacho. Probá `/help` para ver opciones."
        )
        return

    # Indicador "escribiendo..." mientras procesa (Claude puede tardar 5-15s)
    try:
        typing = Activity(type=ActivityTypes.typing)
        await context.send_activity(typing)
    except Exception:
        pass

    try:
        from chat_agent import reply_to
        answer = await asyncio.to_thread(reply_to, text, email)
    except Exception as e:
        logger.exception("Error en chat_agent: %s", e)
        await context.send_activity(
            f"⚠️ Hubo un error procesando tu pregunta: `{e}`. "
            "Si persiste, avisame."
        )
        return

    # Teams tiene límite ~28KB; cortamos si es muy largo
    if len(answer) > 25000:
        answer = answer[:25000] + "\n\n_(respuesta truncada)_"

    await context.send_activity(answer)


async def process_activity(body: dict, auth_header: str) -> None:
    """Procesa una activity recibida via HTTP del Function App."""
    activity = Activity().deserialize(body)
    await adapter.process_activity(activity, auth_header, _on_turn)
