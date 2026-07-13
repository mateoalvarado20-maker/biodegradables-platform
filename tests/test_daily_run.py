"""Tests de M1 — corrida diaria (regla #26: reiniciable/recuperable/supervisable)."""

import pytest

from marketing.daily_run import DailyContext, DailyRunner, hor_stats, log_manual_intervention
from marketing.experiments import ExperimentRegistry
from marketing.pillars import load_pillars
from marketing.pipeline import PipelineServices
from marketing.playbook import Playbook
from marketing.profiles import load_profile
from marketing.queue import ContentQueue
from org.kernel import Charter, Department, TenantStore

from tests.test_broll import _fake_fetch_factory
from tests.test_guionista import _manifest
from tests.test_render_video import _fake_runner_factory
from tests.test_repair import _USAGE, _guion, _review_json
from tests.test_tts import _fake_synth

OBJ_MAP = {"tips-food-service": "leads"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-10"
    )
    dept = Department(_manifest(), charter, store, granted_capabilities={"llm"})
    gen_calls = {"n": 0}

    def gen_llm(system, messages):
        gen_calls["n"] += 1
        return _guion(), _USAGE

    services = PipelineServices(
        profile=load_profile("tiktok"),
        brand_context="ctx",
        voice="voz-test",
        out_dir=tmp_path / "out",
        gen_llm_call=gen_llm,
        review_llm_call=lambda s, m: (_review_json(85), _USAGE),
        synth_fn=_fake_synth,
        fetch_fn=_fake_fetch_factory(),
        runner=_fake_runner_factory([]),
    )
    ctx = DailyContext(
        queue=ContentQueue(dept),
        playbook=Playbook(dept),
        registry=ExperimentRegistry(dept),
        services=services,
        pillars=load_pillars("biodegradables"),
        objective_by_pillar=OBJ_MAP,
        tenant_id="tenant-a",
        n_briefs=2,
    )
    yield dept, ctx, gen_calls
    store.close()


def test_corrida_completa_y_ledger_del_dia(env):
    dept, ctx, _ = env
    runner = DailyRunner(dept, ctx)
    result = runner.run(day="2026-07-11")
    assert result["completa"] is True
    assert result["plan_size"] == 2
    assert result["estados"] == {"qa_approved": 2}
    # evento operativo emitido (métrica del HOR)
    assert dept.events.fetch(types=["ops.daily_run"])


def test_idempotente_por_dia(env):
    dept, ctx, gen_calls = env
    runner = DailyRunner(dept, ctx)
    runner.run(day="2026-07-11")
    llamadas = gen_calls["n"]
    r2 = runner.run(day="2026-07-11")  # mismo día otra vez
    assert gen_calls["n"] == llamadas  # ni una llamada más al LLM
    assert r2["completa"] is True


def test_crash_a_mitad_resume_sin_replanificar(env, tmp_path):
    dept, ctx, gen_calls = env
    runner = DailyRunner(dept, ctx)

    def runner_roto(args):
        raise RuntimeError("render caído")

    ctx.services.runner = runner_roto
    r1 = runner.run(day="2026-07-11")
    assert r1["completa"] is False  # plan hecho, producción atascada
    llamadas = gen_calls["n"]

    # "reinicio del proceso": nueva instancia, servicios sanos
    ctx.services.runner = _fake_runner_factory([])
    runner2 = DailyRunner(dept, ctx)
    r2 = runner2.run(day="2026-07-11")
    assert r2["completa"] is True
    assert gen_calls["n"] == llamadas  # NO re-planificó ni re-generó guiones


def test_status_supervisable_sin_codigo(env):
    dept, ctx, _ = env
    runner = DailyRunner(dept, ctx)
    assert runner.status(day="2026-07-11")["estado"] == "sin corrida"
    runner.run(day="2026-07-11")
    s = runner.status(day="2026-07-11")
    assert s["estado"] == "completa"
    assert len(s["piezas"]) == 2
    assert all(p["status"] == "qa_approved" for p in s["piezas"])
    assert "hor_mes" in s


def test_hor_castiga_intervenciones_declaradas(env):
    dept, ctx, _ = env
    runner = DailyRunner(dept, ctx)
    runner.run(day="2026-07-11")
    # HOR sin intervenciones = 1.0 (nota: los eventos usan fecha UTC real,
    # por eso consultamos el mes corriente)
    h = hor_stats(dept)
    assert h["runs_completos"] == 1 and h["hor"] == 1.0
    log_manual_intervention(dept, "relancé el render a mano")
    h2 = hor_stats(dept)
    assert h2["intervenciones_manuales"] == 1
    assert h2["hor"] == 0.0  # 1 corrida, 1 rescate: nada fue hands-off
    assert any("intervención manual" in e["decision"] for e in dept.journal.entries())