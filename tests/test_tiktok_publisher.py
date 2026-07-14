"""Tests M3.0d — backend TikTok del puerto Publisher + CLI de conexión.
Todo inyectado (cero red)."""

import pytest

from marketing.publisher import PublishError, publish_scheduled
from marketing.queue import ContentQueue
from marketing.tiktok_publisher import DEFAULT_CHUNK, TikTokPublisher, plan_chunks

from tests.test_publisher import ON, _dept, _scheduled_pkg

MB = 1024 * 1024


class StubClient:
    """Doble del TikTokClient con las respuestas del contrato real."""

    def __init__(self, statuses=None, privacy_options=None, init_error=None):
        self.statuses = list(statuses or ["PUBLISH_COMPLETE"])
        self.privacy_options = privacy_options
        self.init_error = init_error
        self.init_kwargs = None

    def creator_info(self, token):
        data = {}
        if self.privacy_options is not None:
            data["privacy_level_options"] = self.privacy_options
        return {"data": data, "error": {"code": "ok"}}

    def video_init_upload(self, token, **kwargs):
        if self.init_error:
            return {"error": {"code": self.init_error, "message": "boom"}}
        self.init_kwargs = kwargs
        return {"data": {"publish_id": "pub-1", "upload_url": "https://up/1"},
                "error": {"code": "ok"}}

    def status_fetch(self, token, publish_id):
        status = self.statuses.pop(0)
        data = {"status": status}
        if status == "PUBLISH_COMPLETE":
            data["publicaly_available_post_id"] = ["777"]
        if status == "FAILED":
            data["fail_reason"] = "video_format_check_failed"
        return {"data": data, "error": {"code": "ok"}}


def _publisher(tmp_path, client, size=1 * MB, **kwargs):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * size)
    chunks = []

    def put_chunk(url, data, headers):
        chunks.append((url, len(data), headers["Content-Range"]))

    pub = TikTokPublisher(
        token_provider=lambda: "tok-1",
        video_path_for=lambda pkg: video,
        client=client,
        put_chunk=put_chunk,
        **kwargs,
    )
    return pub, chunks


def test_plan_chunks_archivo_chico_un_solo_chunk():
    assert plan_chunks(5 * MB) == [(0, 5 * MB - 1)]


def test_plan_chunks_el_ultimo_absorbe_el_remanente():
    size = 75 * MB + 123  # > 64 MB → chunked
    rangos = plan_chunks(size, DEFAULT_CHUNK)
    assert len(rangos) == 7  # floor(75MB/10MB)
    assert rangos[0] == (0, DEFAULT_CHUNK - 1)
    assert rangos[-1][1] == size - 1  # el último llega al final exacto
    # contiguos y sin huecos
    for (_, fin_a), (inicio_b, _) in zip(rangos, rangos[1:]):
        assert inicio_b == fin_a + 1


def test_publica_video_chico_e2e(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)
    client = StubClient(statuses=["PROCESSING_UPLOAD", "PUBLISH_COMPLETE"])
    pub, chunks = _publisher(tmp_path, client)

    out = publish_scheduled(dept, queue, pub, cfg=ON)
    assert out["published"] == 1
    final = queue.get(package.package_id)
    assert final.status == "published"
    assert final.post_ref.post_id == "777"
    assert final.post_ref.privacy == "SELF_ONLY"
    # un solo chunk con el range exacto
    assert chunks == [("https://up/1", 1 * MB, f"bytes 0-{1 * MB - 1}/{1 * MB}")]
    # el init declaró SELF_ONLY y el título lleva caption+hashtags
    assert client.init_kwargs["privacy_level"] == "SELF_ONLY"
    assert client.init_kwargs["total_chunk_count"] == 1


def test_status_failed_es_publish_error(tmp_path):
    client = StubClient(statuses=["FAILED"])
    pub, _ = _publisher(tmp_path, client)
    from marketing.models import PostRef

    with pytest.raises(PublishError, match="video_format_check_failed"):
        pub.fetch_status(PostRef(platform="tiktok", publish_id="pub-1"))


def test_init_con_error_de_api_no_sube_nada(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)
    pub, chunks = _publisher(tmp_path, StubClient(init_error="spam_risk_too_many_posts"))

    out = publish_scheduled(dept, queue, pub, cfg=ON)
    assert out["failed"] == 1
    assert chunks == []  # ni un byte subido
    assert queue.get(package.package_id).status == "publish_failed"


def test_privacidad_no_permitida_no_publica(tmp_path):
    client = StubClient(privacy_options=["PUBLIC_TO_EVERYONE"])  # sin SELF_ONLY
    pub, chunks = _publisher(tmp_path, client)
    with pytest.raises(PublishError, match="SELF_ONLY"):
        # falla en creator_info, ANTES de tocar el video o subir un byte
        pub.init_publish(None)  # type: ignore[arg-type]
    assert chunks == []


def test_video_faltante_falla_claro(tmp_path):
    client = StubClient()
    video = tmp_path / "no-existe.mp4"
    pub = TikTokPublisher(token_provider=lambda: "t",
                          video_path_for=lambda pkg: video, client=client,
                          put_chunk=lambda *a: None)
    with pytest.raises(PublishError, match="no encontrado"):
        pub.init_publish(None)  # type: ignore[arg-type]


# ---------- CLI de conexión ----------

def test_connect_cli_flujo_feliz():
    from marketing.tiktok_connect import connect

    respuestas = [
        {"connected": False},
        {"status": "ok", "authorize_url": "https://tiktok/auth?x=1", "state": "t.s"},
        {"connected": False},
        {"connected": True, "open_id": "o-1", "scopes": "user.info.basic,video.publish"},
    ]
    llamadas = []

    def caller(method, path, payload):
        llamadas.append(path)
        return respuestas.pop(0)

    abiertos, prints = [], []
    ok = connect("biodegradables", caller=caller, opener=abiertos.append,
                 sleeper=lambda s: None, out=prints.append)
    assert ok is True
    assert abiertos == ["https://tiktok/auth?x=1"]
    assert any("Cuenta conectada" in p for p in prints)


def test_connect_cli_ya_conectada_es_noop():
    from marketing.tiktok_connect import connect

    prints = []
    ok = connect("biodegradables",
                 caller=lambda m, p, b: {"connected": True, "open_id": "o"},
                 opener=lambda u: (_ for _ in ()).throw(AssertionError("no abrir")),
                 sleeper=lambda s: None, out=prints.append)
    assert ok is True and any("ya tiene" in p for p in prints)


def test_connect_cli_timeout_no_cuelga():
    from marketing.tiktok_connect import connect

    def caller(method, path, payload):
        if "connect-start" in path:
            return {"authorize_url": "u", "state": "s"}
        return {"connected": False}

    prints = []
    ok = connect("biodegradables", caller=caller, opener=lambda u: None,
                 sleeper=lambda s: None, max_wait_s=10, poll_s=5,
                 out=prints.append)
    assert ok is False and any("agotado" in p for p in prints)
