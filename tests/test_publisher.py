"""Tests M3.0a + M3.0c — puerto Publisher, kill-switch de 3 capas y simulacro
E2E con el fake de TikTok (cero red)."""

import pytest

from marketing.metrics import MetricsStore, PostMetrics
from marketing.publisher import (
    FakeTikTokPublisher,
    NullPublisher,
    PublishingDisabled,
    publish_scheduled,
    publishing_enabled,
)
from marketing.queue import ContentQueue
from org.kernel import Charter, Department, TenantStore, parse_manifest
from org.kernel.department import CapabilityError

from tests.test_daily_run import env  # noqa: F401 (fixture reutilizada)
from tests.test_metrics import _pkg

ON = {"publishing": {"enabled": True}}
OFF = {"publishing": {"enabled": False}}


def _manifest_con_publish():
    return parse_manifest({
        "verops": "0.1",
        "package": {"name": "marketing-brain", "version": "0.1.0",
                    "publisher": "ver-ia", "kind": "department"},
        "trust_tier": "first_party",
        "capabilities": [{"llm": {}}, "notify", "publish"],
        "contracts": {}, "events": {},
        "autonomy": {"max_level": "L2", "default": "L0"},
    })


def _dept(tmp_path, monkeypatch, *, grant_publish: bool):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(okrs=("okr",), budget_usd_month=10.0,
                      approved_by="board@test", approved_at="2026-07-10")
    granted = {"llm", "publish"} if grant_publish else {"llm"}
    return Department(_manifest_con_publish(), charter, store,
                      granted_capabilities=granted)


def _scheduled_pkg(dept, queue):
    package = _pkg(dept).model_copy(update={"status": "scheduled"})
    queue.enqueue(package.model_copy(update={"status": "draft"}))
    queue.save(package)
    return package


# ---------- kill-switch: 3 capas ----------

def test_capa1_flag_apagado_ni_toca_el_backend(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    _scheduled_pkg(dept, queue)
    fake = FakeTikTokPublisher()

    out = publish_scheduled(dept, queue, fake, cfg=OFF)
    assert out == {"skipped": 1, "published": 0, "still_processing": 0, "failed": 0}
    assert fake.init_calls == 0  # el backend NI SE CONSULTÓ
    assert queue.ids_with_status("scheduled")  # la pieza sigue intacta


def test_capa1_default_es_apagado():
    assert publishing_enabled({}) is False
    assert publishing_enabled({"publishing": {}}) is False


def test_capa2_capacidad_no_otorgada_lanza(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=False)
    queue = ContentQueue(dept)
    _scheduled_pkg(dept, queue)
    with pytest.raises(CapabilityError, match="publish"):
        publish_scheduled(dept, queue, FakeTikTokPublisher(), cfg=ON)
    assert queue.ids_with_status("scheduled")  # nada se perdió


def test_capa3_null_publisher_revierte_y_audita(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    _scheduled_pkg(dept, queue)

    out = publish_scheduled(dept, queue, NullPublisher(), cfg=ON)
    assert out["published"] == 0 and out["failed"] == 0
    assert queue.ids_with_status("scheduled")  # revertida, NO consumida
    assert dept.events.fetch(types=["ops.publishing_disabled"])


def test_null_publisher_lanza_publishing_disabled():
    with pytest.raises(PublishingDisabled):
        NullPublisher().init_publish(None)  # type: ignore[arg-type]


# ---------- M3.0c: simulacro E2E con el fake ----------

def test_simulacro_e2e_publica_confirma_y_audita(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)
    fake = FakeTikTokPublisher(polls_needed=2)

    out = publish_scheduled(dept, queue, fake, cfg=ON)
    assert out["published"] == 1
    final = queue.get(package.package_id)
    assert final.status == "published"
    assert final.post_ref.post_id.startswith("fake-post-")
    assert final.post_ref.privacy == "SELF_ONLY"
    assert dept.events.fetch(types=["content.published"])
    assert any("PUBLICADA" in e["decision"] for e in dept.journal.entries())

    # métricas del post entran al MetricsStore (el circuito de M3.2)
    ms = MetricsStore(dept)
    ms.record(package.package_id,
              PostMetrics(captured_at="2026-07-14T12:00", age_hours=1.0,
                          values={"views": 120.0, "shares": 3.0}))
    assert ms.series(package.package_id, "views") == [(1.0, 120.0)]


def test_reintento_no_duplica_posts(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    _scheduled_pkg(dept, queue)
    fake = FakeTikTokPublisher()

    publish_scheduled(dept, queue, fake, cfg=ON)
    publish_scheduled(dept, queue, fake, cfg=ON)  # segunda corrida (retry/catch-up)
    assert fake.init_calls == 1  # UN solo init — jamás doble post


def test_crash_post_init_se_reconcilia_sin_reinit(tmp_path, monkeypatch):
    """Crash tras el init (publish_id ya persistido): la siguiente corrida
    SOLO consulta estado — nunca vuelve a init-ear."""
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)
    fake = FakeTikTokPublisher()
    ref = fake.init_publish(package)  # el init "ocurrió" antes del crash
    queue.save(package.model_copy(update={"status": "publishing", "post_ref": ref}))

    out = publish_scheduled(dept, queue, fake, cfg=ON)
    assert out["published"] == 1
    assert fake.init_calls == 1  # solo el pre-crash
    assert queue.get(package.package_id).status == "published"


def test_crash_pre_init_es_ambiguo_y_no_se_repostea(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)
    queue.save(package.model_copy(update={"status": "publishing"}))  # sin ref

    out = publish_scheduled(dept, queue, FakeTikTokPublisher(), cfg=ON)
    assert out["failed"] == 1
    assert queue.get(package.package_id).status == "publish_failed"
    evs = dept.events.fetch(types=["content.publish_failed"])
    assert any("AMBIGUO" in e.payload.get("reason", "") for e in evs)


def test_init_fallido_marca_failed_y_sigue(tmp_path, monkeypatch):
    dept = _dept(tmp_path, monkeypatch, grant_publish=True)
    queue = ContentQueue(dept)
    package = _scheduled_pkg(dept, queue)

    out = publish_scheduled(dept, queue, FakeTikTokPublisher(fail_init=True), cfg=ON)
    assert out["failed"] == 1
    assert queue.get(package.package_id).status == "publish_failed"


# ---------- integración con la corrida diaria ----------

def test_daily_run_con_flag_apagado_solo_cuenta(env):  # noqa: F811
    """El fixture env no otorga publish ni prende el flag: la corrida termina
    normal reportando la publicación en 0 y sin estados de publicación."""
    from marketing.daily_run import DailyRunner

    dept, ctx, _ = env
    result = DailyRunner(dept, ctx).run(day="2026-07-11")
    assert result["completa"] is True
    assert result["publicacion"]["published"] == 0
    assert "published" not in result["estados"]
