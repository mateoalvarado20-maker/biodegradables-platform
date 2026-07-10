"""Tests de F3.4 — Analista propone / Knowledge Manager decide / Playbook
versionado con madurez (regla #20). Validado contra el simulador sesgado."""

import pytest

from marketing.analista import run_analysis
from marketing.experiments import ExperimentRegistry
from marketing.knowledge import KnowledgeManager
from marketing.metrics import BiasedSimulator
from marketing.playbook import Playbook, PlaybookError
from marketing.scoring import score_piece
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _manifest
from tests.test_metrics import _pkg


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


def _scored(dept, sim, n_pregunta=8, n_lista=8):
    piezas = [_pkg(dept, hook_type="pregunta") for _ in range(n_pregunta)]
    piezas += [_pkg(dept, hook_type="lista") for _ in range(n_lista)]
    return [(p, score_piece(p.package_id, sim(p, 48.0).values, 48.0)) for p in piezas]


def _ciclo(dept, playbook, registry, km, sim, **kw):
    conclusions, proposals = run_analysis(dept, _scored(dept, sim, **kw), registry, playbook.rules())
    decisions = [km.consider(p) for p in proposals]
    promotions = km.review_promotions()
    return conclusions, proposals, decisions, promotions


# --- el criterio central de F3: descubre el sesgo sembrado -------------------------


def test_ciclo_descubre_sesgo_y_crea_regla_experimental(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _, proposals, decisions, _ = _ciclo(dept, playbook, registry, km, sim)

    assert any(p.dimension == "hook_type" and p.value == "pregunta" for p in proposals)
    regla = playbook.get("regla:hook_type=pregunta")
    assert regla is not None and regla["status"] == "experimental"
    # los 8 campos de la propuesta viajaron al journal
    refs = " ".join(r for e in dept.journal.entries() for r in e["context_refs"])
    for campo in ("riesgo de aceptar", "riesgo de no aceptar", "impacto esperado", "reversibilidad"):
        assert campo in refs


def test_control_negativo_no_toca_el_playbook(env):
    dept, playbook, registry, km = env
    # noise=0: el control negativo testea el CAMINO LÓGICO de forma determinista
    # (con ruido y ~2 hipótesis a α≈5%, un falso positivo ocasional es esperable;
    # en producción lo absorbe la escalera de madurez del KM: exige rachas)
    sim = BiasedSimulator(biases={}, noise=0.0)
    _, proposals, _, _ = _ciclo(dept, playbook, registry, km, sim)
    assert proposals == []
    assert playbook.rules() == {}


def test_madurez_experimental_a_validada_a_consolidada(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    # ciclo 1: crea experimental (1 confirmación)
    _ciclo(dept, playbook, registry, km, sim)
    assert playbook.get("regla:hook_type=pregunta")["status"] == "experimental"
    # ciclo 2: 2 confirmaciones consecutivas → validada
    _ciclo(dept, playbook, registry, km, sim)
    assert playbook.get("regla:hook_type=pregunta")["status"] == "validada"
    # ciclos 3-4: 4 consecutivas → consolidada (nunca se salta niveles)
    _ciclo(dept, playbook, registry, km, sim)
    assert playbook.get("regla:hook_type=pregunta")["status"] == "validada"
    _ciclo(dept, playbook, registry, km, sim)
    assert playbook.get("regla:hook_type=pregunta")["status"] == "consolidada"


def test_contradiccion_degrada_de_a_un_nivel(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    for _ in range(4):  # hasta consolidada
        _ciclo(dept, playbook, registry, km, sim)
    assert playbook.get("regla:hook_type=pregunta")["status"] == "consolidada"
    # el mundo cambia: ahora el sesgo se invierte
    sim_invertido = BiasedSimulator(biases={("hook_type", "lista"): 2.0})
    _ciclo(dept, playbook, registry, km, sim_invertido)
    # consolidada NO muere de golpe: baja un nivel
    assert playbook.get("regla:hook_type=pregunta")["status"] == "validada"


def test_experimental_contradicha_muere_directo(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)  # experimental
    sim_invertido = BiasedSimulator(biases={("hook_type", "lista"): 2.5})
    _ciclo(dept, playbook, registry, km, sim_invertido)
    regla = playbook.get("regla:hook_type=pregunta")
    assert regla["status"] == "obsoleta"
    assert regla["rule_id"] not in playbook.rules()  # fuera de las activas


def test_revert_restaura_sin_perder_historial(env):
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)
    _ciclo(dept, playbook, registry, km, sim)  # validada (revisión 2)
    hist_antes = playbook.history("regla:hook_type=pregunta")
    rev = playbook.revert("regla:hook_type=pregunta", 1, reason="prueba del board", decided_by="board")
    regla = playbook.get("regla:hook_type=pregunta")
    assert regla["status"] == "experimental"  # exactamente el estado de la rev 1
    assert regla["revision"] == rev == len(hist_antes) + 1  # historial intacto + 1
    hist = playbook.history("regla:hook_type=pregunta")
    assert [h["revision"] for h in hist] == list(range(1, rev + 1))
    assert "REVERT" in hist[-1]["rationale"]


def test_confianza_baja_rechazada_por_el_km(env):
    dept, playbook, registry, km = env
    from marketing.analista import ChangeProposal

    p = ChangeProposal(
        target_knowledge="NUEVA: hook_type=x",
        proposed_action="priorizar x",
        kind="crear",
        dimension="hook_type",
        value="x",
        evidence_for=["algo"],
        evidence_against=[],
        risks_accept="r1",
        risks_reject="r2",
        expected_impact="i",
        confidence="baja",
        reversibility="alta",
    )
    d = km.consider(p)
    assert not d.accepted and "confianza" in d.rationale
    assert playbook.rules() == {}


def test_regla_20_el_analista_no_importa_el_playbook():
    """Test de capas: el módulo del Analista no puede ni importar el Playbook."""
    import ast
    from pathlib import Path

    src = Path("marketing/analista.py").read_text(encoding="utf-8")
    imports = [
        n.module if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [None])
    ]
    assert not any(i and "playbook" in i for i in imports)
    assert not any(i and "knowledge" in i for i in imports)