"""Notificaciones operativas — M1 (resumen diario + alertas de error).

Envía por `graph_mail` (app-only, funciona desde la PC con las env vars de la
plataforma). REGLA: notificar jamás tumba la corrida — el fallo de envío se
degrada a log + evento `ops.notify_failed`. Destinatarios y remitente son
datos del tenant (`marketing.yaml: daily.notify_to / notify_from`).

`sender` inyectable para tests (regla de la casa: nada de red en pytest).
"""

from __future__ import annotations

import logging
from typing import Callable

from org.kernel.department import Department

logger = logging.getLogger("marketing.notify")

# sender(from_user, to, subject, html_body)
Sender = Callable[[str, list[str], str, str], None]


def _default_sender(from_user: str, to: list[str], subject: str, html_body: str) -> None:
    import graph_mail

    graph_mail.send(from_user, to, subject, html_body)


def _safe_send(dept: Department, sender: Sender | None, from_user: str,
               to: list[str], subject: str, html: str) -> bool:
    if not to or not from_user:
        logger.warning("notify: sin destinatarios/remitente configurados — skip")
        return False
    try:
        (sender or _default_sender)(from_user, to, subject, html)
        return True
    except Exception as exc:
        logger.exception("notify falló (la corrida sigue): %s", exc)
        dept.emit("ops.notify_failed", {"subject": subject[:120], "error": str(exc)[:200]})
        return False


def send_daily_summary(dept: Department, result: dict, pending: list[dict],
                       *, from_user: str, to: list[str],
                       sender: Sender | None = None) -> bool:
    filas = "".join(
        f"<tr><td style='padding:4px 8px'>{p['package_id']}</td>"
        f"<td style='padding:4px 8px'>{p['titulo']}</td>"
        f"<td style='padding:4px 8px'>{p['formato']}</td></tr>"
        for p in pending
    )
    html = (
        f"<p>Corrida diaria <b>{result['day']}</b>: "
        f"{result['plan_size']} piezas — estados {result['estados']} — "
        f"gasto del mes ${result['gasto_mes_usd']}.</p>"
        + (
            "<p><b>Pendientes de tu aprobación L0:</b></p>"
            f"<table border='1' cellspacing='0'>{filas}</table>"
            "<p>Decidir: <code>python -m marketing.daily_run aprobar|rechazar "
            "&lt;package_id&gt; [motivo]</code> (la tarjeta de Teams llega con el "
            "siguiente deploy del bot).</p>"
            if pending
            else "<p>Sin piezas pendientes de aprobación.</p>"
        )
    )
    return _safe_send(dept, sender, from_user, to,
                      f"[VER-IA Marketing] Corrida diaria {result['day']}", html)


def send_alert(dept: Department, subject: str, body: str, *, from_user: str,
               to: list[str], sender: Sender | None = None) -> bool:
    return _safe_send(dept, sender, from_user, to,
                      f"[VER-IA Marketing][ALERTA] {subject}",
                      f"<p>{body}</p><p>Diagnóstico: <code>python -m "
                      "marketing.daily_run status</code></p>")
