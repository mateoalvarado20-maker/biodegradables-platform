"""Tests de F3.5 — el Planificador como Media Manager (regla #22)."""

import random

import pytest

from marketing.experiments import ExperimentRegistry
from marketing.knowledge import KnowledgeManager
from marketing.metrics import BiasedSimulator
from marketing.pillars import load_pillars
from marketing.planner import plan_day
from marketing.playbook import Playbook
from marketing.profiles import load_profile
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _manifest
from tests.test_knowledge import _ciclo


@pytest.fixture
def env(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-10"
    )
    dept = Department(_manifest(), charter, store, granted_capabilities={"llm"})
    playbook = Playbook(dept)
    registry = ExperimentRegistry(dept)
    km = KnowledgeManager(dept, playbook, registry)
    yield dept, playbook, registry, km
    store.close()


PILLARS = load_pillars("biodegradables")
PROFILE = load_profile("tiktok")


def _plan(dept, playbook, registry, n=10, **kw):
    return plan_day(
        dept,
        tenant_id="tenant-a",
        pillars=PILLARS,
        rules=playbook.active_rules(),
        latest_verdicts=registry.latest_verdicts(),
        profile=PROFILE,
        n_briefs=n,
        rng=random.Random(7),
        **kw,
    )


def test_sin_conocimiento_todo_es_exploracion_honesta(env):
    dept, playbook, registry, km = env
    plan = _plan(dept, playbook, registry, n=5)
    assert plan.explore_ratio == 1.0
    assert all(p.intent == "explorar" for p in plan.briefs)
    # cada brief exploratorio dice QUÉ dato produce
    assert all(p.expected_learning for p in plan.briefs)


def test_con_conocimiento_respeta_80_20(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    for _ in range(2):
        _ciclo(dept, playbook, registry, km, sim)  # regla validada
    plan = _plan(dept, playbook, registry, n=10)
    n_explore = sum(1 for p in plan.briefs if p.intent == "explorar")
    assert n_explore == 2  # 20% de 10
    # los briefs de explotación referencian la regla real y su madurez
    exploits = [p for p in plan.briefs if p.intent == "explotar"]
    assert all(p.knowledge_used == ["regla:hook_type=pregunta"] for p in exploits)
    assert all("validada" in p.rationale for p in exploits)
    # y su hipótesis alimenta el KPI LA
    assert all("LA" in p.brief.hypothesis.decision_if_false for p in exploits)


def test_exploracion_sale_de_los_huecos_del_registro(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 3.0})
    # muestra chica → el registro queda con 'requiere_mas_datos'
    _ciclo(dept, playbook, registry, km, sim, n_pregunta=3, n_lista=8)
    assert registry.latest_verdicts()["hook_type=pregunta"]["verdict"] == "requiere_mas_datos"
    plan = _plan(dept, playbook, registry, n=5)
    exploradores = [p for p in plan.briefs if p.intent == "explorar"]
    # el primer explorador ataca exactamente ese hueco de evidencia
    assert any(
        p.brief.hook_type == "pregunta" and "requiere_mas_datos" in p.rationale
        for p in exploradores
    )


def test_todo_brief_tiene_proposito_completo(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)
    plan = _plan(dept, playbook, registry, n=10)
    for p in plan.briefs:
        assert p.intent in ("explotar", "explorar")
        assert p.rationale and p.expected_learning
        assert p.brief.hypothesis.question and p.brief.hypothesis.decision_if_false
    # mezcla de formatos: 1 de cada 5 es carrusel
    assert sum(1 for p in plan.briefs if p.brief.format == "carousel") == 2


def test_explain_responde_las_preguntas_del_board(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    for _ in range(2):
        _ciclo(dept, playbook, registry, km, sim)
    plan = _plan(dept, playbook, registry, n=5)
    texto = plan.explain()
    for fragmento in ("PLAN:", "explora", "por qué:", "hipótesis:",
                      "conocimiento explotado:", "aprendemos aunque rinda poco:"):
        assert fragmento in texto
    # y quedó auditado en el journal
    assert any("plan diario" in e["decision"] for e in dept.journal.entries())


def test_plan_determinista_con_rng_sembrado(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)
    a = _plan(dept, playbook, registry, n=6)
    b = _plan(dept, playbook, registry, n=6)
    assert [p.brief.hook_type for p in a.briefs] == [p.brief.hook_type for p in b.briefs]


def test_sin_pilares_no_hay_plan(env):
    dept, playbook, registry, km = env
    with pytest.raises(ValueError, match="pilares"):
        plan_day(
            dept, tenant_id="t", pillars=[], rules=[], latest_verdicts={},
            profile=PROFILE, n_briefs=2,
        )