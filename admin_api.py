"""admin_api — endpoints /admin/* de la plataforma (F4.4b VER-IA).

Extraídos de teams_bot.py el 2026-07-04 (auditoría H2: god-file). Son los
39 endpoints de operación/testing manual, todos protegidos por
_require_admin (X-Admin-Token). teams_bot los monta con include_router al
FINAL de su módulo — por eso el `from teams_bot import ...` de abajo es
seguro: cuando este módulo se importa, teams_bot ya está completamente
definido. No importar admin_api desde ningún otro lugar.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter()

import logging  # noqa: E402

logger = logging.getLogger("admin_api")

# Módulos de dominio — import directo (no via teams_bot).
import activity_state  # noqa: E402
import ask_agent  # noqa: E402
import core_config  # noqa: E402
import tenant_roles  # noqa: E402
import monthly_recap  # noqa: E402
import news_brief  # noqa: E402
import reminders  # noqa: E402
from botbuilder.core import TurnContext  # noqa: E402
from botbuilder.schema import Activity, ConversationReference  # noqa: E402

# Símbolos del bot que usan los handlers. Seguro por el orden de montaje
# (ver docstring); la completitud la valida ruff F821 en CI.
from teams_bot import (  # noqa: E402
    AAD_OVERRIDE,
    ACTIVITIES_APP_ID,
    CIERRE_CAJA_USERS,
    JOSE_EMAIL,
    REFS_PATH,
    SUCURSAL_POR_USER,
    _AAD_LOOKUP_PATH,
    _REFS_LOCK,
    _allowed_email_senders,
    _build_apertura_caja_card,
    _build_checkin_card,
    _build_confirmacion_cierre_card,
    _build_jose_asistencia_card,
    _build_jose_ruta_card,
    _job_consolidated_daily,
    _jose_summary_html,
    _load_aad_lookup,
    _load_refs,
    _require_admin,
    _run_daily_report_test,
    _save_aad_lookup,
    _save_refs,
    activities_adapter,
    auto_assign_cobranzas,
    deliver_due_reminders,
    generate_daily_news_brief,
    scheduler,
    send_daily_checkin,
    send_morning_sales_report_job,
    send_task_confirmations_job,
    send_weekly_summaries,
    sync_task_calendar_events_job,
)

@router.post("/admin/trigger-reply-agent")
async def trigger_reply_agent(request: Request, since_hours: int = 24) -> dict[str, Any]:
    """F4.3: corre el reply agent ahora (validación post-cutover).
    ?since_hours=N para ampliar la ventana (default 24)."""
    _require_admin(request)
    import reply_agent
    resumen = await asyncio.to_thread(
        reply_agent.process_inbox, since_hours=since_hours
    )
    return {"status": "ok", "resumen": resumen}


@router.get("/admin/llm-usage")
async def admin_llm_usage(request: Request, month: str | None = None) -> dict[str, Any]:
    """F3 (VER-IA): resumen del consumo de IA del tenant — total del mes,
    desglose por agente, por modelo y por día, y estado del presupuesto
    (LLM_BUDGET_MONTHLY_USD). ?month=YYYY-MM para meses anteriores."""
    _require_admin(request)
    import llm_usage
    s = llm_usage.summary(month)
    s["budget"] = llm_usage.budget_status(month)
    return s


@router.post("/admin/trigger-checkin")
async def trigger_checkin(request: Request) -> dict[str, Any]:
    _require_admin(request)
    await send_daily_checkin()
    refs = _load_refs()
    return {
        "status": "triggered",
        "users": list(refs.get("activities", {}).keys()),
    }


@router.post("/admin/trigger-reminders")
async def trigger_reminders(request: Request) -> dict[str, Any]:
    """Forzar entrega de reminders vencidos ahora (testing)."""
    _require_admin(request)
    await deliver_due_reminders()
    return {
        "status": "triggered",
        "pending_count": len(reminders.list_reminders(only_pending=True)),
    }


@router.post("/admin/trigger-cobranzas")
async def trigger_cobranzas(request: Request) -> dict[str, Any]:
    """Forzar auto-asignación de cobranzas ahora (testing).

    Fire-and-forget: el pull de Contifico tarda ~2 min y bloquearía la request
    más allá del timeout del gateway (502). Se lanza en background y se responde
    al toque; el resultado se ve en el Log stream ('auto_assign_cobranzas: N
    asignadas') y en el check-in del colaborador. Fix 2026-06-23."""
    _require_admin(request)

    # F0 (2026-07-02): auto_assign_cobranzas ahora LANZA en fallo total (para
    # que _reliable_job alerte) — el trigger manual la envuelve para no dejar
    # una task con excepción sin recoger.
    async def _run() -> None:
        try:
            await auto_assign_cobranzas()
        except Exception:
            logger.exception("trigger-cobranzas manual falló")

    asyncio.create_task(_run())
    return {"status": "started", "nota": "corre en background ~1-2 min"}


@router.post("/admin/trigger-weekly-summaries")
async def trigger_weekly_summaries(request: Request) -> dict[str, Any]:
    """Forzar envío de weekly summaries ahora (testing)."""
    _require_admin(request)
    await send_weekly_summaries()
    return {"status": "triggered"}


@router.post("/admin/trigger-task-confirmations")
async def trigger_task_confirmations(request: Request) -> dict[str, Any]:
    """Forzar el envío de cards de confirmación de tareas ahora (testing)."""
    _require_admin(request)
    await send_task_confirmations_job()
    return {"status": "triggered"}


@router.post("/admin/trigger-team-workload")
async def trigger_team_workload(request: Request) -> dict[str, Any]:
    """Forzar el envío del roll-up de carga del equipo ahora (testing)."""
    _require_admin(request)
    result = await asyncio.to_thread(ask_agent.send_team_workload_summary)
    return {"status": "triggered", **result}


@router.post("/admin/trigger-calendar-sync")
async def trigger_calendar_sync(request: Request) -> dict[str, Any]:
    """Forzar el sync de eventos de calendario ahora (testing). Funciona aunque
    CALENDAR_SYNC_ENABLED esté apagado — útil para validar tras el admin consent."""
    _require_admin(request)
    await sync_task_calendar_events_job()
    return {"status": "triggered"}


@router.post("/admin/trigger-news-brief")
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


@router.post("/admin/trigger-sales-recap")
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


@router.post("/admin/trigger-activities-recap")
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


@router.post("/admin/trigger-midmonth-status")
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


@router.post("/admin/seed-template-for-user")
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


@router.get("/admin/show-activities-for-user")
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


@router.post("/admin/preview-checkin-as-user")
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
        "send_to_email", core_config.MIO
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


@router.get("/admin/aad-lookup")
async def admin_aad_lookup_get(request: Request) -> dict[str, Any]:
    """Phase V (2026-06-11): muestra el lookup AAD ID → email aprendido +
    overrides activos. Útil para auditar quién está mapeado a quién."""
    _require_admin(request)
    return {
        "learned_lookup": _load_aad_lookup(),
        "env_overrides": AAD_OVERRIDE,
        "lookup_path": str(_AAD_LOOKUP_PATH),
    }


@router.post("/admin/aad-lookup/set")
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


@router.post("/admin/aad-lookup/remove")
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


@router.post("/admin/trigger-morning-sales-job")
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


@router.post("/admin/trigger-sales-report-test")
async def trigger_sales_report_test_admin(request: Request) -> dict[str, Any]:
    """Dispara el reporte comercial en modo TEST (envía SOLO a Mateo) para
    validar cómo llega el correo — incluida la nueva columna de gestión de
    cobranza en la sección de cartera. Fire-and-forget (daily_report consulta
    Contifico ~2 min; si se esperara, gunicorn daría 502)."""
    _require_admin(request)
    asyncio.create_task(asyncio.to_thread(_run_daily_report_test))
    return {
        "status": "started",
        "nota": "reporte de prueba a Mateo (malvarado@) en ~2 min",
    }


@router.post("/admin/set-chocolates")
async def set_chocolates_admin(request: Request) -> dict[str, Any]:
    """Corrige el stock de chocolates de un colaborador para la semana actual.

    Body: {"user_email": "info@...", "cantidad": 8}
    Override limpio (stock_actual == cantidad), para corregir confusiones.
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    email = (body or {}).get("user_email", "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="falta user_email")
    try:
        cantidad = int((body or {}).get("cantidad"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="cantidad inválida")
    activity_state.corregir_chocolates_stock(email, cantidad)
    rec = activity_state.get_chocolates_semana(email)
    return {"ok": True, "user": email, "chocolates": rec}


@router.post("/admin/preview-jose-route")
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
        "send_to_email", core_config.MIO
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


@router.post("/admin/preview-jose-asistencia")
async def preview_jose_asistencia(request: Request) -> dict[str, Any]:
    """Dispara el card de ASISTENCIA de José al ref de `send_to_email`
    (default: malvarado@) para previsualizarlo. Body: {"send_to_email": "..."}."""
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    send_to_email = (body or {}).get(
        "send_to_email", core_config.MIO
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
            "📋 **PREVIEW** — card de asistencia de José (17:10 Lun-Vie / 12:00 Sáb):"
        )
        await turn_context.send_activity(_build_jose_asistencia_card(JOSE_EMAIL))
        await turn_context.send_activity(
            "_(Preview — si apretás Guardar, la asistencia queda en el state de JOSÉ.)_"
        )

    await activities_adapter.continue_conversation(
        ref, cb, bot_id=ACTIVITIES_APP_ID
    )
    return {"status": "sent", "to": send_to_email}


@router.post("/admin/preview-jose-summary-email")
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
        "to_override", core_config.MIO
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


@router.post("/admin/send-message-to-users")
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


@router.post("/admin/schedule-one-time-message")
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


@router.post("/admin/schedule-one-time-email")
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

    # F0 (2026-07-02): remitente restringido — sin esto el endpoint permitía
    # enviar desde CUALQUIER buzón del tenant (spoofing interno).
    if from_user.lower() not in _allowed_email_senders():
        raise HTTPException(
            status_code=403,
            detail="from_user no permitido (gerencia/operador; ampliar con "
                   "ADMIN_EMAIL_FROM_ALLOWLIST)",
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


@router.post("/admin/preview-apertura-caja")
async def preview_apertura_caja(request: Request) -> dict[str, Any]:
    """Phase S: preview del card matinal de apertura de caja.

    Body: {as_email (info@ o quito@), send_to_email (default malvarado@)}
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    as_email = (body or {}).get("as_email", tenant_roles.INFO_EMAIL).strip().lower()
    send_to = (body or {}).get(
        "send_to_email", core_config.MIO
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


@router.post("/admin/preview-confirmacion-cierre")
async def preview_confirmacion_cierre(request: Request) -> dict[str, Any]:
    """Phase P: preview del card de confirmación de cierre que llega al validador.

    Body: {emisor_email, fecha, send_to_email (override del validador real)}
    """
    _require_admin(request)

    try:
        body = await request.json()
    except Exception:
        body = {}
    emisor = (body or {}).get("emisor_email", tenant_roles.INFO_EMAIL).strip().lower()
    fecha = (body or {}).get("fecha", activity_state._today().isoformat()).strip()
    send_to = (body or {}).get("send_to_email", core_config.MIO).strip().lower()

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


@router.post("/admin/trigger-consolidated-daily-summary")
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


@router.post("/admin/run-consolidated-daily-now")
async def run_consolidated_daily_now(request: Request) -> dict[str, Any]:
    """Dispara el consolidado diario REAL ahora — a los destinatarios normales
    (Daniel + Gabriela, CC Mateo) y PASANDO POR EL LEDGER. A diferencia de
    /admin/trigger-consolidated-daily-summary (que saltea el ledger para tests),
    este marca 'ya enviado hoy', así el cron de las 18:30 se saltea solo y NO
    duplica. La programación sigue normal mañana."""
    _require_admin(request)
    ran = await _job_consolidated_daily()
    return {
        "ok": True,
        "enviado": ran,
        "nota": (
            "Consolidado enviado a los destinatarios normales y ledger marcado; "
            "el job de las 18:30 se saltea hoy."
            if ran else
            "No se envió (el ledger ya estaba marcado para hoy — ya salió)."
        ),
    }


@router.post("/admin/trigger-saturday-recap")
async def trigger_saturday_recap(request: Request) -> dict[str, Any]:
    """Dispara el recap del sábado ahora (testing). Reporta el sábado anterior.

    Body opcional: {"to_override": ["..."], "cc_override": ["..."]}.
    """
    _require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    to_override = (body or {}).get("to_override")
    cc_override = (body or {}).get("cc_override")
    from ask_agent import send_saturday_recap_summary
    result = await asyncio.to_thread(
        send_saturday_recap_summary, to_override, cc_override
    )
    return result


@router.post("/admin/reset-day-for-user")
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


@router.post("/admin/wipe-user-from-activities")
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


@router.post("/admin/add-activity-for-user")
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


@router.post("/admin/remove-activity-for-user")
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


@router.post("/admin/set-priorities-for-user")
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


@router.get("/admin/state-debug")
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


