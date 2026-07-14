"""Puente PC ↔ bot para la aprobación L0 en Teams (M1, 2026-07-14).

El pipeline corre en la PC y el bot en el App Service — no comparten disco.
La PC nunca toca el Azure Table de decisiones directamente: habla con el
admin API del bot (3 endpoints `/admin/marketing/l0-*`). Config por env vars:

    VERIA_BOT_BASE_URL  p.ej. https://biodegradables-bot-app-....azurewebsites.net
    ADMIN_API_TOKEN     el mismo X-Admin-Token del App Service

REGLA (igual que notify.py): nada de este módulo tumba la corrida. Sin
config, o con el bot caído, se degrada a log + evento `ops.l0_remote_failed`
y la aprobación sigue disponible por CLI. `caller` inyectable para tests.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

from marketing.approvals import ApprovalError, approve, reject
from marketing.queue import ContentQueue
from org.kernel.department import Department

logger = logging.getLogger("marketing.l0_remote")

# caller(method, path, payload|None) -> dict (respuesta JSON del bot)
Caller = Callable[[str, str, dict | None], dict]


def _bot_config() -> tuple[str, str] | None:
    base = os.environ.get("VERIA_BOT_BASE_URL", "").strip().rstrip("/")
    token = os.environ.get("ADMIN_API_TOKEN", "").strip()
    if not base or not token:
        return None
    return base, token


def _default_caller(method: str, path: str, payload: dict | None) -> dict:
    import httpx

    base, token = _bot_config()  # type: ignore[misc]  # ya validado por quien llama
    resp = httpx.request(
        method, f"{base}{path}",
        headers={"X-Admin-Token": token},
        json=payload, timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _pieza_for_card(queue: ContentQueue, package_id: str) -> dict:
    p = queue.get(package_id)
    dur = round(p.word_timings[-1].end_ms / 1000, 1) if p.word_timings else None
    return {
        "package_id": package_id,
        "titulo": p.title[:120],
        "formato": p.labels.format,
        "duracion_s": dur,
        "hook": p.hook[:200],
        "caption": p.caption_master[:300],
    }


def push_pending_cards(
    dept: Department,
    queue: ContentQueue,
    package_ids: list[str],
    recipients: list[str],
    *,
    caller: Caller | None = None,
) -> dict:
    """Manda las piezas pendientes al bot para que reparta las tarjetas L0.
    Nunca lanza; devuelve un dict con qué pasó (para log/telemetría)."""
    if not package_ids:
        return {"status": "sin pendientes"}
    if not recipients:
        return {"status": "skip", "motivo": "sin aprobadores configurados"}
    if caller is None and _bot_config() is None:
        logger.info(
            "l0_remote: VERIA_BOT_BASE_URL/ADMIN_API_TOKEN no configurados — "
            "las tarjetas de Teams quedan deshabilitadas (aprobación por CLI)"
        )
        return {"status": "skip", "motivo": "sin config del bot"}
    try:
        piezas = [_pieza_for_card(queue, pid) for pid in package_ids]
        out = (caller or _default_caller)(
            "POST", "/admin/marketing/l0-cards",
            {"piezas": piezas, "recipients": recipients},
        )
        logger.info("l0_remote: tarjetas enviadas: %s", out.get("entregas"))
        return out
    except Exception as exc:
        logger.exception("l0_remote: push de tarjetas falló (la corrida sigue)")
        dept.emit("ops.l0_remote_failed",
                  {"op": "push_cards", "error": str(exc)[:200]})
        return {"status": "error", "error": str(exc)[:200]}


def apply_remote_decisions(
    dept: Department,
    queue: ContentQueue,
    *,
    caller: Caller | None = None,
) -> dict:
    """Trae las decisiones tomadas en Teams y las aplica a la cola local con
    las MISMAS funciones que el CLI (approve/reject — auditoría idéntica).
    Nunca lanza. Una decisión que la cola ya no permite (p.ej. ya se decidió
    por CLI) se marca aplicada igual: es redundante, no un error."""
    if caller is None and _bot_config() is None:
        return {"status": "skip", "motivo": "sin config del bot"}
    try:
        decisiones = (caller or _default_caller)(
            "GET", "/admin/marketing/l0-decisions", None
        ).get("decisiones", {})
    except Exception as exc:
        logger.exception("l0_remote: no pude leer decisiones (la corrida sigue)")
        dept.emit("ops.l0_remote_failed",
                  {"op": "get_decisions", "error": str(exc)[:200]})
        return {"status": "error", "error": str(exc)[:200]}

    aplicadas: list[str] = []
    redundantes: list[str] = []
    errores: dict[str, str] = {}
    for pid, d in decisiones.items():
        by = f"teams:{d.get('decided_by', '')}"
        try:
            if d.get("decision") == "aprobar":
                approve(dept, queue, pid, by=by)
            else:
                reject(dept, queue, pid, by=by,
                       reason=d.get("motivo") or "rechazada desde Teams")
            aplicadas.append(pid)
        except ApprovalError as exc:
            logger.warning("l0_remote: decisión %s redundante/tardía: %s", pid, exc)
            redundantes.append(pid)
        except Exception as exc:
            # No confirmamos al bot: reintenta en la próxima corrida
            logger.exception("l0_remote: aplicar %s falló", pid)
            errores[pid] = str(exc)[:200]

    confirmables = aplicadas + redundantes
    if confirmables:
        try:
            (caller or _default_caller)(
                "POST", "/admin/marketing/l0-applied",
                {"package_ids": confirmables},
            )
        except Exception as exc:
            logger.exception("l0_remote: confirmar aplicadas falló — el bot las "
                             "reofrecerá y approve/reject las tratará como redundantes")
            dept.emit("ops.l0_remote_failed",
                      {"op": "mark_applied", "error": str(exc)[:200]})
    resultado = {
        "status": "ok",
        "aplicadas": aplicadas,
        "redundantes": redundantes,
        "errores": errores,
    }
    if aplicadas or redundantes or errores:
        logger.info("l0_remote: decisiones de Teams: %s", resultado)
    return resultado
