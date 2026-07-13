"""Tests de F3.6 — KPIs LV+LA y reporte semanal (reglas #18 y #23)."""

import pytest

from marketing.learning_report import (
    learning_accuracy,
    learning_velocity,
    render_report,
    weekly_learning_report,
)
from marketing.metrics import BiasedSimulator
from marketing.scoring import OBJECTIVE_WEIGHTS, score_piece

from tests.test_knowledge import _ciclo, env  # noqa: F401 (fixture reutilizada)

DESDE = "2000-01-01"


def test_scoring_pondera_distinto_por_objetivo():
    """Regla #23: una pieza de conversación no se mide como una de awareness."""
    latest = {"views": 1000, "shares": 50, "comments": 40, "saves": 30, "likes": 80,
              "follower_delta": 10}
    scores = {obj: score_piece("pkg-x" * 3, latest, 48.0, objective=obj).score
              for obj in OBJECTIVE_WEIGHTS}
    assert scores["conversations"] != scores["awareness"] != scores["sales"]
    assert len(set(scores.values())) >= 3  # perfiles realmente distintos


def test_lv_cuenta_evaluaciones_y_cambios_del_playbook(env):  # noqa: F811
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)
    _ciclo(dept, playbook, registry, km, sim)  # 2ª confirmación → promovida

    lv = learning_velocity(registry, playbook, DESDE)
    assert lv["hypotheses_evaluated"] >= 4  # pregunta+lista × 2 ciclos
    assert lv["confirmada"] >= 2
    assert lv["playbook_changes"]["nacidas"] == 1
    assert lv["playbook_changes"]["promovidas"] == 1


def test_la_mide_supervivencia_de_confirmadas(env):  # noqa: F811
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    _ciclo(dept, playbook, registry, km, sim)
    la0 = learning_accuracy(registry)
    assert la0["la"] is None  # sin re-evaluaciones aún: "no sé", no "100%"

    _ciclo(dept, playbook, registry, km, sim)  # confirmada → confirmada
    la1 = learning_accuracy(registry)
    assert la1["la"] == 1.0 and la1["confirmations_reevaluated"] >= 1

    # el mundo cambia: la confirmación anterior NO sobrevive
    sim_invertido = BiasedSimulator(biases={("hook_type", "lista"): 2.5})
    _ciclo(dept, playbook, registry, km, sim_invertido)
    la2 = learning_accuracy(registry)
    assert la2["la"] < 1.0  # el KPI castiga el conocimiento que hubo que corregir


def test_reporte_semanal_responde_las_preguntas_del_board(env):  # noqa: F811
    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    # semana con incertidumbre resuelta: primero n chico, luego n completo
    _ciclo(dept, playbook, registry, km, sim, n_pregunta=3, n_lista=8)
    _ciclo(dept, playbook, registry, km, sim)
    # y una creencia retirada
    sim_invertido = BiasedSimulator(biases={("hook_type", "lista"): 2.5})
    _ciclo(dept, playbook, registry, km, sim_invertido)

    r = weekly_learning_report(dept, registry, playbook, DESDE)
    assert any("hook_type=pregunta" in a for a in r["que_aprendimos"])
    assert r["que_dejamos_de_creer"]  # la confirmada que no sobrevivió
    assert r["reglas_nuevas"]
    # retorno de aprendizaje: requiere_mas_datos → confirmada quedó registrado
    assert any("requiere_mas_datos → confirmada" in x for x in r["mayor_retorno_de_aprendizaje"])
    # valor comercial: las piezas del fixture son objetivo 'leads'
    assert any("leads/" in v for v in r["valor_comercial"])
    # LV nunca sin LA (regla #18)
    assert "lv" in r and "la" in r
    # y quedó en el journal
    assert any("reporte de aprendizaje" in e["decision"] for e in dept.journal.entries())

    texto = render_report(r)
    for pregunta in ("¿Qué aprendimos?", "¿Qué dejamos de creer?", "Reglas nuevas",
                     "Mayor retorno de aprendizaje", "Valor comercial", "LV:", "LA:"):
        assert pregunta in texto


def test_segmentacion_por_objetivo_en_el_conocimiento(env):  # noqa: F811
    """Regla #23 E2E: la misma dimensión bajo objetivos distintos produce
    conocimiento SEPARADO (jamás se comparan como equivalentes)."""
    from marketing.analista import run_analysis
    from tests.test_metrics import _pkg
    from tests.test_guionista import _brief

    dept, playbook, registry, km = env
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})

    piezas = []
    for objective in ("leads", "awareness"):
        for hook in ("pregunta", "lista"):
            for _ in range(6):
                # _pkg usa _brief (objective=leads); override por model_copy
                from marketing.guionista import generate_package
                from marketing.profiles import load_profile
                from tests.test_repair import _USAGE, _guion

                brief = _brief().model_copy(update={"hook_type": hook, "objective": objective})
                piezas.append(
                    generate_package(dept, brief, load_profile("tiktok"), "ctx",
                                     llm_call=lambda s, m: (_guion(), _USAGE))
                )
    scored = [
        (p, score_piece(p.package_id, sim(p, 48.0).values, 48.0, objective=p.labels.objective))
        for p in piezas
    ]
    conclusions, proposals = run_analysis(dept, scored, registry, playbook.rules())
    for p in proposals:
        km.consider(p)

    verdicts = registry.latest_verdicts()
    assert "leads/hook_type=pregunta" in verdicts
    assert "awareness/hook_type=pregunta" in verdicts  # evaluadas POR SEPARADO
    reglas = playbook.rules()
    # el sesgo aplica en ambos objetivos → puede haber regla en cada uno,
    # pero SIEMPRE como conocimiento independiente
    assert all(r["objective"] in ("leads", "awareness") for r in reglas.values())