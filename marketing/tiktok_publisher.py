"""Backend TikTok del puerto Publisher — M3.0d (código sin credenciales).

Implementa el contrato de `marketing/publisher.py` contra la Content Posting
API real vía FILE_UPLOAD (los MP4 viven en la PC — PULL_FROM_URL exigiría
URL pública con dominio verificado). NO se instancia en producción hasta
M3.1: `bootstrap` sigue armando NullPublisher y las 3 capas del kill-switch
siguen puestas — este módulo solo deja el camino listo para que activar sea
configuración, no código.

Inyectables (cero red en pytest): `token_provider` (en producción será el
GET /admin/marketing/tiktok/token del bot), `client` (TikTokClient con http
fake) y `put_chunk` (subida de bytes).

Reglas de plataforma que este módulo respeta:
- `creator_info` se consulta ANTES de cada post (lo exige TikTok) y se
  valida que el nivel de privacidad pedido esté permitido.
- Pre-auditoría TikTok fuerza SELF_ONLY; lo pedimos explícito igual.
- Chunks: tamaño fijo por chunk y el ÚLTIMO absorbe el remanente
  (total_chunk_count = floor(size / chunk_size), mínimo 1).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from marketing.models import ContentPackage, PostRef
from marketing.publisher import PublishError
from tiktok_connector import TikTokClient

logger = logging.getLogger("marketing.tiktok_publisher")

DEFAULT_CHUNK = 10 * 1024 * 1024  # 10 MB (rango permitido 5-64 MB)
MAX_SINGLE = 64 * 1024 * 1024  # hasta 64 MB puede ir en un solo chunk

# estados de status_fetch que significan "seguí esperando"
_IN_PROGRESS = {"PROCESSING_UPLOAD", "PROCESSING_DOWNLOAD", "SEND_TO_USER_INBOX"}


def plan_chunks(size: int, chunk: int = DEFAULT_CHUNK) -> list[tuple[int, int]]:
    """Rangos (inicio, fin_inclusive) por chunk según la regla de TikTok:
    N-1 chunks exactos y el último absorbe el remanente."""
    if size <= 0:
        raise PublishError("archivo de video vacío")
    if size <= MAX_SINGLE:
        return [(0, size - 1)]
    total = max(1, size // chunk)
    rangos = []
    for i in range(total):
        inicio = i * chunk
        fin = size - 1 if i == total - 1 else inicio + chunk - 1
        rangos.append((inicio, fin))
    return rangos


def _default_put_chunk(url: str, data: bytes, headers: dict) -> None:
    import httpx

    resp = httpx.put(url, content=data, headers=headers, timeout=120.0)
    if resp.status_code not in (200, 201, 206):
        raise PublishError(f"subida de chunk falló: HTTP {resp.status_code}")


def _ok(payload: dict, contexto: str) -> dict:
    """Valida el envelope de error de la Content Posting API."""
    err = (payload.get("error") or {})
    code = err.get("code", "ok")
    if code != "ok":
        raise PublishError(f"{contexto}: {code} — {err.get('message', '')}"[:300])
    return payload.get("data") or {}


class TikTokPublisher:
    platform = "tiktok"

    def __init__(
        self,
        *,
        token_provider: Callable[[], str],
        video_path_for: Callable[[ContentPackage], Path],
        client: TikTokClient | None = None,
        put_chunk: Callable[[str, bytes, dict], None] | None = None,
        chunk_size: int = DEFAULT_CHUNK,
        privacy_level: str = "SELF_ONLY",  # M3.3 lo cambia SOLO con acta del board
    ):
        self._token = token_provider
        self._video_path_for = video_path_for
        self._client = client or TikTokClient()
        self._put_chunk = put_chunk or _default_put_chunk
        self._chunk_size = chunk_size
        self._privacy = privacy_level

    def _title(self, package: ContentPackage) -> str:
        tags = " ".join(f"#{h}" for h in package.hashtags_master)
        return f"{package.caption_master} {tags}".strip()[:2200]

    def init_publish(self, package: ContentPackage) -> PostRef:
        token = self._token()

        # TikTok exige creator_info antes de cada post; además valida caps
        info = _ok(self._client.creator_info(token), "creator_info")
        opciones = info.get("privacy_level_options") or []
        if opciones and self._privacy not in opciones:
            raise PublishError(
                f"la cuenta no permite privacy_level={self._privacy} "
                f"(opciones: {opciones}) — no se intenta publicar"
            )

        path = self._video_path_for(package)
        if not path.exists():
            raise PublishError(f"video no encontrado: {path}")
        size = path.stat().st_size
        rangos = plan_chunks(size, self._chunk_size)

        data = _ok(self._client.video_init_upload(
            token,
            title=self._title(package),
            video_size=size,
            chunk_size=(size if len(rangos) == 1 else self._chunk_size),
            total_chunk_count=len(rangos),
            privacy_level=self._privacy,
        ), "video_init")
        publish_id = data.get("publish_id", "")
        upload_url = data.get("upload_url", "")
        if not publish_id or not upload_url:
            raise PublishError("init sin publish_id/upload_url — respuesta inesperada")

        with open(path, "rb") as fh:
            for inicio, fin in rangos:
                fh.seek(inicio)
                blob = fh.read(fin - inicio + 1)
                self._put_chunk(upload_url, blob, {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(blob)),
                    "Content-Range": f"bytes {inicio}-{fin}/{size}",
                })
        logger.info("tiktok: %s subido (%d bytes, %d chunks) publish_id=%s",
                    package.package_id, size, len(rangos), publish_id)
        return PostRef(platform=self.platform, publish_id=publish_id,
                       privacy=self._privacy)

    def fetch_status(self, ref: PostRef) -> PostRef:
        data = _ok(self._client.status_fetch(self._token(), ref.publish_id),
                   "status_fetch")
        status = data.get("status", "")
        if status == "PUBLISH_COMPLETE":
            ids = data.get("publicaly_available_post_id") or data.get("post_id") or []
            post_id = str(ids[0]) if isinstance(ids, list) and ids else str(ids or "")
            return ref.model_copy(update={
                "post_id": post_id,
                "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
        if status in _IN_PROGRESS:
            return ref
        # FAILED o desconocido → falla explícita (jamás asumir éxito)
        raise PublishError(
            f"publicación falló: status={status} razón={data.get('fail_reason', '')}"[:300]
        )
