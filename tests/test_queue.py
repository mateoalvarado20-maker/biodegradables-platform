"""Tests de F2.0d — cola persistente + runner crash-safe (todo inyectado)."""

import json

import pytest

from marketing.guionista import ScriptBrief
from marketing.models import Hypothesis
from marketing.pipeline import PipelineServices, advance, run_pending, submit
from marketing.profiles import load_profile
from marketing.queue import ContentQueue, QueueError
from org.kernel import Charter, Department, TenantStore

from tests.test_broll import _fake_fetch_factory
from tests.test_guionista import _manifest
from tests.test_render_video import _fake_runner_factory
from tests.test_repair import _USAGE, _guion, _review_json
from tests.test_tts import _fake_synth


@pytest.fixture
def env(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-10"
    )
    dept = Department(_manifest(), charter, store, granted_capabilities={"llm"})
    services = PipelineServices(
        profile=load_profile("tiktok"),
        brand_context="ctx",
        voice="voz-test",
        out_dir=tmp_path / "out",
        gen_llm_call=lambda s, m: (_guion(), _USAGE),
        review_llm_call=lambda s, m: (_review_json(85), _USAGE),
        synth_fn=_fake_synth,
        fetch_fn=_fake_fetch_factory(),
        runner=_fake_runner_factory([]),
    )
    yield dept, ContentQueue(dept), services
    store.close()


def _brief():
    return ScriptBrief(
        tenant_id="tenant-a",
        pillar_id="tips-food-service",
        format="video",
        hook_type="lista",
        cta_type="contacto",
        time_slot="18:00-21:00",
        objective="leads",
        hypothesis=Hypothesis(
            question="¿la cola persiste y reanuda correctamente?",
            metric="views",
            success_criteria="> mediana",
            decision_if_true="seguir",
            decision_if_false="revisar",
        ),
    )


def test_submit_encola_copy_approved(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    assert r.approved
    assert queue.stats() == {"copy_approved": 1}
    # brief persistido para auditoría
    assert queue.get_brief(r.package.package_id)["pillar_id"] == "tips-food-service"


def test_run_pending_lleva_a_terminal(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    stats = run_pending(dept, queue, services)
    assert stats == {"qa_approved": 1}
    final = queue.get(r.package.package_id)
    assert final.status == "qa_approved"
    assert any(a.kind == "video" and a.scene_index is None for a in final.assets)
    # F3.9: el staging del render se limpia tras el gate final
    from marketing.render_video import RENDER_DIR

    assert not (RENDER_DIR / "public" / r.package.package_id).exists()


def test_crash_a_mitad_reanuda_sin_duplicar(env, tmp_path):
    """El criterio del board: kill a mitad de lote → reanudar sin duplicar ni perder."""
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    pid = r.package.package_id

    # etapa 1 (producción) con un runner que "muere" tras escribir el estado
    advance(dept, queue, pid, services)
    assert queue.get(pid).status == "produced"
    renders_tras_etapa1 = dept.meter.month_units("render")

    # "CRASH": el proceso muere aquí. Simulamos re-arranque creando una cola
    # nueva sobre el MISMO store (como haría un proceso nuevo).
    queue2 = ContentQueue(dept)
    assert queue2.get(pid).status == "produced"  # nada se perdió

    # reanudar: run_pending solo ejecuta la etapa que falta (gate final)
    stats = run_pending(dept, queue2, services)
    assert stats == {"qa_approved": 1}
    # sin duplicar: el render NO volvió a correr
    assert dept.meter.month_units("render") == renders_tras_etapa1


def test_error_de_etapa_queda_registrado_y_reintenta(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    pid = r.package.package_id

    def runner_roto(args):
        raise RuntimeError("compositor murió")

    servicios_rotos = PipelineServices(**{**services.__dict__, "runner": runner_roto})
    stats = run_pending(dept, queue, servicios_rotos)
    assert stats == {"copy_approved": 1}  # no avanzó, no se perdió
    attempts, last_error = queue.attempts(pid)
    assert attempts == 1 and "compositor" in last_error
    # siguiente run con servicios sanos: reanuda y termina
    stats = run_pending(dept, queue, services)
    assert stats == {"qa_approved": 1}


def test_tras_max_errores_queda_en_revision_manual(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)

    def runner_roto(args):
        raise RuntimeError("siempre falla")

    servicios_rotos = PipelineServices(**{**services.__dict__, "runner": runner_roto})
    for _ in range(4):
        run_pending(dept, queue, servicios_rotos)
    attempts, _ = queue.attempts(r.package.package_id)
    assert attempts == 3  # MAX_STAGE_ATTEMPTS: no reintenta infinito
    assert queue.stats() == {"copy_approved": 1}


def test_rechazo_definitivo_tambien_se_persiste(env):
    dept, queue, services = env
    servicios_rechazo = PipelineServices(
        **{
            **services.__dict__,
            "review_llm_call": lambda s, m: (_review_json(40, blockers=["defecto"]), _USAGE),
        }
    )
    r = submit(dept, queue, _brief(), servicios_rechazo)
    assert not r.approved
    assert queue.stats() == {"qa_rejected": 1}  # el rechazo también es dato
    assert run_pending(dept, queue, services) == {"qa_rejected": 1}  # terminal: no se toca


def test_enqueue_duplicado_y_get_inexistente(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    with pytest.raises(QueueError, match="ya está en la cola"):
        queue.enqueue(r.package)
    with pytest.raises(QueueError, match="no está en la cola"):
        queue.get("pkg-inexistente")


def test_roundtrip_preserva_el_package_completo(env):
    dept, queue, services = env
    r = submit(dept, queue, _brief(), services)
    recuperado = queue.get(r.package.package_id)
    assert recuperado == r.package  # pydantic: igualdad campo a campo
    assert json.loads(recuperado.model_dump_json())["hypothesis"]["metric"] == "views"