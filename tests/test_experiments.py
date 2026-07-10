"""Tests de F3.2+F3.3 — scoring + experimentos con los 4 veredictos (regla #19).

El criterio central: contra el simulador, el sesgo SEMBRADO se confirma, el
inexistente NO se confirma (control negativo), y la muestra chica produce
'requiere_mas_datos' — el sistema sabe cuándo no sabe."""

import pytest

from marketing.experiments import (
    ExperimentRegistry,
    evaluate_hypothesis,
)
from marketing.metrics import BiasedSimulator
from marketing.scoring import ScoringError, maturity_factor, score_piece
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _manifest
from tests.test_metrics import _pkg


@pytest.fixture
def dept(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-10"
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


def _scored(dept, sim, n_grupo, n_resto, age=48.0):
    piezas = [_pkg(dept, hook_type="pregunta") for _ in range(n_grupo)]
    piezas += [_pkg(dept, hook_type="lista") for _ in range(n_resto)]
    out = []
    for p in piezas:
        m = sim(p, age)
        out.append((p, score_piece(p.package_id, m.values, age)))
    return out


# --- scoring (F3.2) ------------------------------------------------------------------


def test_score_normaliza_por_madurez(dept):
    sim = BiasedSimulator(noise=0.0)
    pkg = _pkg(dept)
    joven = score_piece(pkg.package_id, sim(pkg, 24.0).values, 24.0)
    maduro = score_piece(pkg.package_id, sim(pkg, 72.0).values, 72.0)
    # misma pieza a edades distintas → score proyectado comparable (±5%)
    assert joven.projected_views == pytest.approx(maduro.projected_views, rel=0.05)


def test_score_rechaza_senal_temprana_y_sin_views(dept):
    with pytest.raises(ScoringError, match="temprana"):
        score_piece("pkg-x", {"views": 100}, age_hours=3.0)
    with pytest.raises(ScoringError, match="sin views"):
        score_piece("pkg-x", {}, age_hours=48.0)
    assert maturity_factor(72.0) > 0.94


# --- los 4 veredictos (regla #19) ------------------------------------------------------


def test_sesgo_sembrado_se_confirma(dept):
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    c = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 8, 8))
    assert c.verdict == "confirmada"
    assert c.confidence in ("media", "alta")
    assert c.effect > 0.5
    # regla #19: los 5 campos obligatorios presentes
    assert c.sample_size == {"grupo": 8, "resto": 8}
    assert c.evidence and c.next_data_needed
    assert isinstance(c.confounders, list)


def test_control_negativo_no_confirma(dept):
    """Sin sesgo sembrado, el sistema NO debe 'descubrir' un aprendizaje.
    noise=0 para determinismo (los falsos positivos estadísticos de producción
    los absorbe la escalera de madurez del Knowledge Manager)."""
    sim = BiasedSimulator(biases={}, noise=0.0)
    c = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 8, 8))
    assert c.verdict != "confirmada"
    assert c.verdict in ("rechazada", "inconclusa", "requiere_mas_datos")


def test_muestra_chica_requiere_mas_datos(dept):
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 3.0})
    c = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 3, 8))
    # aunque el sesgo sea ENORME, con n=3 el sistema dice "no sé todavía"
    assert c.verdict == "requiere_mas_datos"
    assert "faltan" in c.next_data_needed


def test_sesgo_invertido_se_rechaza(dept):
    """La hipótesis direccional con efecto opuesto → rechazada (también es hallazgo)."""
    sim = BiasedSimulator(biases={("hook_type", "lista"): 2.0})  # el OTRO grupo rinde más
    c = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 8, 8))
    assert c.verdict == "rechazada"
    assert c.effect < 0


def test_confusor_detectado_baja_confianza(dept):
    """Si TODO el grupo comparte otra dimensión, se reporta el confusor."""
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.5})
    grupo = [_pkg(dept, hook_type="pregunta", pillar="producto-en-uso") for _ in range(6)]
    resto = [_pkg(dept, hook_type="lista", pillar="educacion-sostenibilidad") for _ in range(6)]
    scored = [(p, score_piece(p.package_id, sim(p, 48.0).values, 48.0)) for p in grupo + resto]
    c = evaluate_hypothesis("hook_type", "pregunta", scored)
    assert c.confounders  # pillar difiere sistemáticamente entre grupos
    assert "pillar" in c.confounders[0]
    assert c.confidence != "alta"  # el confusor impide confianza alta


# --- registro e historial (base del KPI LA) --------------------------------------------


def test_registro_historial_y_journal(dept):
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    reg = ExperimentRegistry(dept)
    c1 = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 3, 8))
    reg.record(c1)
    c2 = evaluate_hypothesis("hook_type", "pregunta", _scored(dept, sim, 8, 8))
    reg.record(c2)

    hist = reg.history("hook_type=pregunta")
    assert [h["verdict"] for h in hist] == ["requiere_mas_datos", "confirmada"]
    assert reg.latest_verdicts()["hook_type=pregunta"]["verdict"] == "confirmada"
    # auditoría: cada evaluación queda en el journal con confusores y datos-que-faltan
    entries = dept.journal.entries()
    assert any("experimento hook_type=pregunta" in e["decision"] for e in entries)
    refs = " ".join(r for e in entries for r in e["context_refs"])
    assert "datos que faltan" in refs