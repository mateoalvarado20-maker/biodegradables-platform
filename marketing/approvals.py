"""Aprobación L0 — M1 (gobernanza por diseño, no cuenta contra el HOR).

Las piezas `qa_approved` quedan PENDIENTES de decisión humana. La decisión
transiciona en la cola: aprobar → `scheduled` (lista para publicar cuando
exista publisher) / rechazar → `qa_rejected` con motivo. Todo con auditoría
(journal + evento) y quién decidió.

v1: decisión por CLI (`python -m marketing.daily_run aprobar|rechazar <id>`)
con notificación por correo. v2 (post-merge, PR separado): tarjeta Adaptive
en Teams que llama a estas MISMAS funciones vía un endpoint del bot — el
mecanismo no cambia, solo la superficie.
"""

from __future__ import annotations

from marketing.queue import ContentQueue
from org.kernel.department import Department


class ApprovalError(RuntimeError):
    pass


def pending_approval(queue: ContentQueue) -> list[str]:
    """Piezas que esperan la decisión L0."""
    return queue.ids_with_status("qa_approved")


def approve(dept: Department, queue: ContentQueue, package_id: str, *, by: str) -> None:
    package = queue.get(package_id)
    if package.status != "qa_approved":
        raise ApprovalError(
            f"{package_id} está en {package.status!r} — solo se aprueba lo qa_approved"
        )
    queue.save(package.model_copy(update={"status": "scheduled"}))
    dept.decide(
        f"L0 APROBADA {package_id} por {by} → scheduled (lista para publicar)",
        context_refs=[f"título: {package.title[:80]}"],
        correlation_id=package_id,
    )
    dept.emit("content.l0_approved", {"package_id": package_id, "by": by},
              correlation_id=package_id)


def reject(dept: Department, queue: ContentQueue, package_id: str, *, by: str,
           reason: str) -> None:
    if not reason or not reason.strip():
        raise ApprovalError("rechazar exige motivo (auditoría L0)")
    package = queue.get(package_id)
    if package.status != "qa_approved":
        raise ApprovalError(
            f"{package_id} está en {package.status!r} — solo se rechaza lo qa_approved"
        )
    queue.save(package.model_copy(update={"status": "qa_rejected"}))
    dept.decide(
        f"L0 RECHAZADA {package_id} por {by}: {reason[:200]}",
        context_refs=[f"título: {package.title[:80]}"],
        correlation_id=package_id,
    )
    dept.emit("content.l0_rejected",
              {"package_id": package_id, "by": by, "reason": reason[:200]},
              correlation_id=package_id)
