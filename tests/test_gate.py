"""Tests de F1.7 — gate de calidad (LLM inyectado, sin red)."""

import json

import pytest

from marketing.brand import load_hard_rules
from marketing.broll import fetch_broll_for_package
from marketing.gate import deterministic_checks, review_package
from marketing.models import WordTiming
from marketing.profiles import load_profile
from marketing.render_video import render_package
from marketing.tts import synthesize_package
from org.kernel import Charter, Department, TenantStore

from tests.test_broll import _fake_fetch_factory
from tests.test_guionista import _manifest
from tests.test_render_video import _fake_runner_factory
from tests.test_tts import _fake_synth, _package_con_escenas

HARD_RULES = {"claims_prohibidos": ["cero impacto ambiental", "aprobado por la fda"]}


@pytest.fixture
def dept(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",),
        budget_usd_month=10.0,
        approved_by="board@test",
        approved_at="2026-07-07",
        hard_rules=HARD_RULES,
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


def _timings_20s(n_scenes=2):
    """word timings fake que suman ~24 s hablados (dentro del estándar)."""
    out = []
    for i in range(n_scenes):
        out += [
            WordTiming(scene_index=i, word=f"w{j}", start_ms=j * 1000.0, end_ms=j * 1000.0 + 900)
            for j in range(12)
        ]
    return out


@pytest.fixture
def pkg_producido(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    pkg = synthesize_package(dept, pkg, "voz", tmp_path / "voz", synth_fn=_fake_synth)
    pkg = fetch_broll_for_package(dept, pkg, tmp_path / "broll", fetch_fn=_fake_fetch_factory())
    pkg = render_package(dept, pkg, tmp_path / "final", runner=_fake_runner_factory([]))
    # los timings fake de _fake_synth suman <18s → reemplazar por unos de ~24s
    return pkg.model_copy(update={"word_timings": _timings_20s()})


def _approve_llm(score=90):
    def call(system, messages):
        return (
            json.dumps({"score": score, "approved": score >= 75, "reasons": ["ok"], "claim_issues": []}),
            {"input_tokens": 50, "output_tokens": 30},
        )

    return call


# --- checks deterministas -------------------------------------------------------


def test_claim_prohibido_rechaza_sin_llm(dept, pkg_producido, tmp_path):
    con_claim = pkg_producido.model_copy(
        update={"caption_master": "Nuestro bowl tiene CERO IMPACTO AMBIENTAL garantizado"}
    )
    llamadas = []
    out = review_package(
        dept, con_claim, load_profile("tiktok"), "ctx", llm_call=lambda s, m: llamadas.append(1)
    )
    assert out.status == "qa_rejected"
    assert llamadas == []  # rechazo determinista: ni un token gastado
    razones = " ".join(
        r for e in dept.journal.entries() for r in e["context_refs"] if "razón" in r
    )
    assert "claim prohibido" in razones
    evs = dept.events.fetch(types=["content.qa_rejected"])
    assert evs and evs[-1].payload["package_id"] == con_claim.package_id


def test_duracion_fuera_de_estandar_rechaza(dept, pkg_producido):
    largo = pkg_producido.model_copy(
        update={
            "word_timings": [
                WordTiming(scene_index=0, word=f"w{j}", start_ms=j * 1000.0, end_ms=j * 1000.0 + 900)
                for j in range(40)  # ~40 s hablados
            ]
        }
    )
    problems = deterministic_checks(largo, load_profile("tiktok"), HARD_RULES)
    assert any("duración" in p and "20-30" in p for p in problems)


def test_estado_no_producido_rechaza(dept, tmp_path):
    borrador = _package_con_escenas(dept)
    problems = deterministic_checks(borrador, load_profile("tiktok"), HARD_RULES)
    assert any("produced" in p for p in problems)


def test_video_sin_render_final_rechaza(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    pkg = synthesize_package(dept, pkg, "voz", tmp_path, synth_fn=_fake_synth)
    sin_render = pkg.model_copy(update={"status": "produced", "word_timings": _timings_20s()})
    problems = deterministic_checks(sin_render, load_profile("tiktok"), HARD_RULES)
    assert any("sin video final" in p for p in problems)


# --- revisor LLM ------------------------------------------------------------------


def test_pieza_limpia_aprueba_con_revisor(dept, pkg_producido):
    out = review_package(dept, pkg_producido, load_profile("tiktok"), "ctx", llm_call=_approve_llm(92))
    assert out.status == "qa_approved"
    assert dept.events.fetch(types=["content.qa_passed"])
    # score quedó en telemetría
    from marketing.telemetry import stage_stats

    assert stage_stats(dept)["gate"]["score"] == 92


def test_score_bajo_rechaza(dept, pkg_producido):
    out = review_package(dept, pkg_producido, load_profile("tiktok"), "ctx", llm_call=_approve_llm(60))
    assert out.status == "qa_rejected"


def test_revisor_reintenta_json_invalido(dept, pkg_producido):
    respuestas = iter(["no-json", json.dumps({"score": 88, "approved": True, "reasons": []})])

    def call(system, messages):
        return next(respuestas), {"input_tokens": 10, "output_tokens": 10}

    out = review_package(dept, pkg_producido, load_profile("tiktok"), "ctx", llm_call=call)
    assert out.status == "qa_approved"


def test_hard_rules_del_tenant_real():
    rules = load_hard_rules("biodegradables")
    assert "cero impacto ambiental" in rules["claims_prohibidos"]
