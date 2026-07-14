"""Puerto Publisher — M3.0a (plan aprobado por el board 2026-07-14).

Abstrae la red social destino detrás de una interfaz mínima para poder
cambiar de backend (TikTok directo, Ayrshare, la próxima red) sin tocar el
pipeline. REGLA DE ORO del board: NO se publica nada hasta M3.1 (privado) /
M3.3 (público, con acta). El kill-switch tiene TRES capas independientes:

1. `publishing.enabled: false` en `tenants/<slug>/marketing.yaml` — si está
   apagado, `publish_scheduled()` ni siquiera itera: loguea y sale.
2. Capacidad `publish` del kernel — declarada en el manifest pero NO otorgada
   por el bootstrap: aunque alguien prenda el flag, `dept.ensure_capability`
   lanza `CapabilityError` (invariante #2: sin autoridad ambiente).
3. `NullPublisher` es el backend por defecto — sin backend real registrado
   explícitamente, publicar lanza `PublishingDisabled` (auditado).

Anti doble-posteo (diseñado ahora, se paga en M3.1): la transición es
scheduled → publishing (con `publish_id` persistido apenas el init responde)
→ published. Si crasheamos en `publishing`:
- CON publish_id → se reconcilia consultando el estado (nunca re-init).
- SIN publish_id → AMBIGUO (el init pudo o no llegar a la red): pasa a
  `publish_failed` + evento — jamás se re-postea solo (regla: un post
  duplicado en la cuenta del cliente es peor que uno faltante).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Protocol

from marketing.models import ContentPackage, PostRef
from marketing.queue import ContentQueue
from org.kernel.department import Department

logger = logging.getLogger("marketing.publisher")


class PublishError(RuntimeError):
    pass


class PublishingDisabled(PublishError):
    """El kill-switch está puesto — publicar es imposible por diseño."""


class Publisher(Protocol):
    """Contrato del backend de publicación. `init` debe devolver el
    publish_id lo ANTES posible (se persiste antes de confirmar)."""

    platform: str

    def init_publish(self, package: ContentPackage) -> PostRef: ...

    def fetch_status(self, ref: PostRef) -> PostRef:
        """Devuelve el ref actualizado; `published_at` no vacío = confirmado."""
        ...


class NullPublisher:
    """Backend por defecto: publicar NO existe. Capa 3 del kill-switch."""

    platform = "null"

    def init_publish(self, package: ContentPackage) -> PostRef:
        raise PublishingDisabled(
            "publicación deshabilitada (NullPublisher) — habilitar requiere "
            "backend explícito + capacidad 'publish' + flag del tenant (M3.1)"
        )

    def fetch_status(self, ref: PostRef) -> PostRef:
        raise PublishingDisabled("NullPublisher no tiene posts que consultar")


class FakeTikTokPublisher:
    """Doble de TikTok para el simulacro M3.0c y los tests: reproduce el
    contrato real (init devuelve publish_id; el estado tarda `polls_needed`
    consultas en confirmar, como el PROCESSING real) sin tocar la red."""

    platform = "fake"

    def __init__(self, polls_needed: int = 1, fail_init: bool = False):
        self.posts: dict[str, dict] = {}
        self.init_calls = 0
        self._polls_needed = polls_needed
        self._fail_init = fail_init

    def init_publish(self, package: ContentPackage) -> PostRef:
        self.init_calls += 1
        if self._fail_init:
            raise PublishError("fake: init falló (simulado)")
        publish_id = f"fake-pub-{package.package_id}"
        self.posts[publish_id] = {"polls": 0, "package_id": package.package_id}
        return PostRef(platform=self.platform, publish_id=publish_id,
                       privacy="SELF_ONLY")

    def fetch_status(self, ref: PostRef) -> PostRef:
        post = self.posts.get(ref.publish_id)
        if post is None:
            raise PublishError(f"fake: publish_id desconocido {ref.publish_id}")
        post["polls"] += 1
        if post["polls"] < self._polls_needed:
            return ref  # sigue PROCESSING
        return ref.model_copy(update={
            "post_id": f"fake-post-{post['package_id']}",
            "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })


def publishing_enabled(cfg: dict) -> bool:
    """Capa 1: el flag del tenant (`publishing.enabled` del marketing.yaml)."""
    return bool((cfg.get("publishing") or {}).get("enabled", False))


def publish_scheduled(
    dept: Department,
    queue: ContentQueue,
    publisher: Publisher,
    *,
    cfg: dict,
    max_polls: int = 10,
) -> dict:
    """Publica lo `scheduled` respetando las 3 capas del kill-switch, y
    reconcilia lo que quedó `publishing` de una corrida anterior. Devuelve
    el resumen {published, still_processing, failed, skipped}."""
    if not publishing_enabled(cfg):
        n = len(queue.ids_with_status("scheduled"))
        if n:
            logger.info(
                "publicación deshabilitada por el tenant — %d piezas quedan en "
                "scheduled (correcto en M3.0)", n,
            )
        return {"skipped": n, "published": 0, "still_processing": 0, "failed": 0}

    # Capa 2: el kernel debe haber otorgado la capacidad explícitamente
    dept.ensure_capability("publish")

    resumen = {"skipped": 0, "published": 0, "still_processing": 0, "failed": 0}

    # 1) Reconciliar publishing en vuelo (crash-safe, jamás re-init)
    for pid in queue.ids_with_status("publishing"):
        package = queue.get(pid)
        if package.post_ref is None:
            queue.save(package.model_copy(update={"status": "publish_failed"}))
            dept.emit("content.publish_failed",
                      {"package_id": pid, "reason": "publishing sin publish_id "
                       "(crash pre-init): AMBIGUO — resolver a mano, no se re-postea"},
                      correlation_id=pid)
            resumen["failed"] += 1
            continue
        resumen_pid = _poll_until_final(dept, queue, publisher, package, max_polls)
        resumen[resumen_pid] += 1

    # 2) Publicar lo scheduled
    for pid in queue.ids_with_status("scheduled"):
        package = queue.get(pid)
        # publishing SIN ref primero: si crasheamos dentro del init, la pieza
        # queda marcada en vuelo y NUNCA se vuelve a init-ear sola
        queue.save(package.model_copy(update={"status": "publishing"}))
        try:
            ref = publisher.init_publish(package)
        except PublishingDisabled as exc:
            # Capa 3: backend Null — misconfig, NO un fallo de la pieza.
            # Se revierte a scheduled (nada se pierde) y se audita UNA vez.
            queue.save(package.model_copy(update={"status": "scheduled"}))
            dept.emit("ops.publishing_disabled",
                      {"reason": str(exc)[:200],
                       "scheduled": len(queue.ids_with_status("scheduled"))})
            resumen["skipped"] = len(queue.ids_with_status("scheduled"))
            return resumen
        except PublishError as exc:
            queue.save(package.model_copy(update={"status": "publish_failed"}))
            dept.emit("content.publish_failed",
                      {"package_id": pid, "reason": str(exc)[:200]},
                      correlation_id=pid)
            resumen["failed"] += 1
            continue
        package = package.model_copy(update={"status": "publishing", "post_ref": ref})
        queue.save(package)  # publish_id persistido ANTES de confirmar
        resumen[_poll_until_final(dept, queue, publisher, package, max_polls)] += 1
    return resumen


def _poll_until_final(dept, queue, publisher, package, max_polls: int) -> str:
    """Consulta el estado hasta confirmar o agotar. Devuelve la clave del
    resumen: published | still_processing | failed."""
    ref = package.post_ref
    for _ in range(max_polls):
        try:
            ref = publisher.fetch_status(ref)
        except PublishError as exc:
            queue.save(package.model_copy(update={"status": "publish_failed"}))
            dept.emit("content.publish_failed",
                      {"package_id": package.package_id, "reason": str(exc)[:200]},
                      correlation_id=package.package_id)
            return "failed"
        if ref.published_at:
            queue.save(package.model_copy(update={"status": "published", "post_ref": ref}))
            dept.decide(
                f"PUBLICADA {package.package_id} en {ref.platform} "
                f"(privacy={ref.privacy or 'n/a'}, post={ref.post_id})",
                correlation_id=package.package_id,
            )
            dept.emit("content.published",
                      {"package_id": package.package_id, "platform": ref.platform,
                       "post_id": ref.post_id, "privacy": ref.privacy},
                      correlation_id=package.package_id)
            return "published"
    # sigue en proceso: queda `publishing` CON ref — la próxima corrida reconcilia
    queue.save(package.model_copy(update={"status": "publishing", "post_ref": ref}))
    return "still_processing"
