"""send_ledger — registro persistente de envíos de reportes (Fase 3).

Garantiza la propiedad que la auditoría encontró ausente en TODOS los
reportes (S9): **un reporte identificado por (report_key, fecha) se envía
EXACTAMENTE una vez**, sin importar cuántas veces se dispare el job
(retry tras fallo ambiguo de Graph, doble capa de scheduling, re-enable
accidental de una tarea vieja, catch-up post-deploy).

Uso típico en un job:

    fecha = hoy_ec_iso()
    if not send_ledger.claim("morning_sales", fecha):
        return  # ya enviado (o en curso por otro worker)
    try:
        ...generar y enviar...
        send_ledger.confirm("morning_sales", fecha)
    except Exception:
        send_ledger.release("morning_sales", fecha)   # permite reintento
        raise

Estados de una entrada: "claimed" (en curso) → "sent" (confirmado).
Un claim huérfano (proceso murió a mitad del envío) expira a los
CLAIM_TTL_MINUTES y puede reclamarse de nuevo.

El ledger vive en STATE_DIR (en el App Service: /home/.claude-agent,
compartido entre instancias) usando safe_json (atómico + lock + backup).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

logger = logging.getLogger("send_ledger")

LOCAL_TZ = timezone(timedelta(hours=-5))  # Ecuador, sin DST
LEDGER_PATH = (
    Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent"))
    / "send_ledger.json"
)
CLAIM_TTL_MINUTES = 30   # claim sin confirmar más viejo que esto = huérfano
RETENTION_DAYS = 90      # entradas más viejas se purgan


def _now() -> datetime:
    return datetime.now(LOCAL_TZ)


def today_iso() -> str:
    return _now().date().isoformat()


def _key(report_key: str, fecha: str) -> str:
    return f"{report_key}:{fecha}"


def _default() -> dict[str, Any]:
    return {"entries": {}}


def _prune(data: dict[str, Any]) -> None:
    cutoff = (_now() - timedelta(days=RETENTION_DAYS)).date().isoformat()
    entries = data.get("entries", {})
    stale = [k for k in entries if k.rsplit(":", 1)[-1] < cutoff]
    for k in stale:
        del entries[k]


def claim(report_key: str, fecha: str | None = None) -> bool:
    """Reclama el envío. True = adelante (nadie lo envió ni lo está enviando).

    False = ya fue enviado hoy, o hay un claim activo de otro worker.
    """
    fecha = fecha or today_iso()
    k = _key(report_key, fecha)
    result = {"granted": False}

    def mutate(data: dict[str, Any]) -> None:
        _prune(data)
        entries = data.setdefault("entries", {})
        entry = entries.get(k)
        if entry:
            if entry.get("status") == "sent":
                return  # ya enviado — no se concede
            claimed_at = entry.get("claimed_at", "")
            try:
                age = _now() - datetime.fromisoformat(claimed_at)
            except (ValueError, TypeError):
                age = timedelta(days=999)
            if age < timedelta(minutes=CLAIM_TTL_MINUTES):
                return  # claim activo de otro intento — no se concede
            logger.warning(
                "send_ledger: claim huérfano de %s (edad %s) — re-reclamando",
                k, age,
            )
        entries[k] = {
            "status": "claimed",
            "claimed_at": _now().isoformat(timespec="seconds"),
        }
        result["granted"] = True

    safe_json.locked_update(LEDGER_PATH, _default, mutate)
    if not result["granted"]:
        logger.info("send_ledger: %s NO concedido (ya enviado o en curso)", k)
    return result["granted"]


def confirm(report_key: str, fecha: str | None = None, detail: str = "") -> None:
    """Marca el envío como completado. Idempotente."""
    fecha = fecha or today_iso()
    k = _key(report_key, fecha)

    def mutate(data: dict[str, Any]) -> None:
        entries = data.setdefault("entries", {})
        entries[k] = {
            "status": "sent",
            "sent_at": _now().isoformat(timespec="seconds"),
            "detail": detail,
        }

    safe_json.locked_update(LEDGER_PATH, _default, mutate)
    logger.info("send_ledger: %s confirmado", k)


def release(report_key: str, fecha: str | None = None) -> None:
    """Libera un claim tras un fallo, para permitir el reintento."""
    fecha = fecha or today_iso()
    k = _key(report_key, fecha)

    def mutate(data: dict[str, Any]) -> None:
        entries = data.setdefault("entries", {})
        entry = entries.get(k)
        if entry and entry.get("status") == "claimed":
            del entries[k]

    safe_json.locked_update(LEDGER_PATH, _default, mutate)


def already_sent(report_key: str, fecha: str | None = None) -> bool:
    fecha = fecha or today_iso()
    data = safe_json.load_json(LEDGER_PATH, _default)
    entry = data.get("entries", {}).get(_key(report_key, fecha))
    return bool(entry and entry.get("status") == "sent")


def status_today() -> dict[str, Any]:
    """Snapshot de hoy, para el heartbeat / endpoint de salud."""
    data = safe_json.load_json(LEDGER_PATH, _default)
    hoy = today_iso()
    return {
        k: v for k, v in data.get("entries", {}).items() if k.endswith(hoy)
    }
