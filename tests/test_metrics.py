"""Tests de F3.1 — métricas con propósito + simulador con sesgos sembrados."""

import pytest

from marketing.metrics import (
    PURPOSES,
    BiasedSimulator,
    MetricsError,
    MetricsStore,
    PostMetrics,
)
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _manifest
from tests.test_repair import _USAGE, _guion


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


def _pkg(dept, hook_type="pregunta", pillar="tips-food-service"):
    from marketing.guionista import generate_package
    from marketing.profiles import load_profile

    from tests.test_guionista import _brief

    brief = _brief().model_copy(update={"hook_type": hook_type, "pillar_id": pillar})
    return generate_package(
        dept, brief, load_profile("tiktok"), "ctx", llm_call=lambda s, m: (_guion(), _USAGE)
    )


# --- regla #16: dato sin pregunta no se almacena -----------------------------------


def test_toda_metrica_tiene_pregunta():
    assert all(q.strip().startswith("¿") for q in PURPOSES.values())


def test_metrica_sin_proposito_rechazada():
    with pytest.raises(MetricsError, match="regla #16"):
        PostMetrics(captured_at="2026-07-10", age_hours=1.0, values={"impresiones_raras": 5})


def test_series_de_metrica_desconocida_rechazada(dept):
    store = MetricsStore(dept)
    with pytest.raises(MetricsError):
        store.series("pkg-x", "watch_time")  # documentada como NO disponible


# --- snapshots -----------------------------------------------------------------------


def test_snapshot_roundtrip_y_serie_temporal(dept):
    store = MetricsStore(dept)
    for age in (1.0, 24.0, 72.0):
        store.record(
            "pkg-a" * 3,
            PostMetrics(
                captured_at=f"2026-07-10T{int(age):02d}", age_hours=age,
                values={"views": age * 100, "shares": age},
            ),
        )
    serie = store.series("pkg-a" * 3, "views")
    assert serie == [(1.0, 100.0), (24.0, 2400.0), (72.0, 7200.0)]
    assert store.latest("pkg-a" * 3) == {"views": 7200.0, "shares": 72.0}
    assert store.packages_with_metrics() == ["pkg-a" * 3]


# --- simulador con sesgos sembrados ---------------------------------------------------


def test_simulador_determinista(dept):
    pkg = _pkg(dept)
    sim = BiasedSimulator()
    a, b = sim(pkg, 24.0), sim(pkg, 24.0)
    assert a.values == b.values  # mismo package + edad → mismas métricas


def test_sesgo_sembrado_es_detectable(dept):
    """El ground truth del Analista: piezas con el gancho sesgado rinden más."""
    sim = BiasedSimulator(biases={("hook_type", "pregunta"): 2.0})
    con_sesgo = [_pkg(dept, hook_type="pregunta") for _ in range(6)]
    sin_sesgo = [_pkg(dept, hook_type="lista") for _ in range(6)]
    media_con = sum(sim(p, 48.0).values["views"] for p in con_sesgo) / 6
    media_sin = sum(sim(p, 48.0).values["views"] for p in sin_sesgo) / 6
    # con noise ±15% y multiplicador 2.0, la separación debe ser clara
    assert media_con > media_sin * 1.5


def test_sin_sesgo_no_hay_separacion_sistematica(dept):
    """Control negativo: sin sesgo sembrado, las medias deben ser comparables."""
    sim = BiasedSimulator(biases={}, noise=0.0)
    grupo_a = [_pkg(dept, hook_type="pregunta") for _ in range(8)]
    grupo_b = [_pkg(dept, hook_type="lista") for _ in range(8)]
    media_a = sum(sim(p, 48.0).values["views"] for p in grupo_a) / 8
    media_b = sum(sim(p, 48.0).values["views"] for p in grupo_b) / 8
    assert 0.8 < media_a / media_b < 1.25


def test_curva_de_maduracion_crece_y_satura(dept):
    pkg = _pkg(dept)
    sim = BiasedSimulator()
    v1, v24, v72, v200 = (sim(pkg, h).values["views"] for h in (1, 24, 72, 200))
    assert v1 < v24 < v72 < v200
    assert (v200 - v72) < (v24 - v1)  # saturación