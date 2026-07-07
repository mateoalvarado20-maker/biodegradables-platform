"""Tests de F1.2 — Guionista (sin red: llm_call inyectado)."""

import json

import pytest

from marketing.brand import load_brand_context
from marketing.guionista import MODEL, ScriptBrief, generate_package
from marketing.models import Hypothesis
from marketing.profiles import load_profile
from org.kernel import Charter, Department, TenantStore, parse_manifest
from org.kernel.metering import BudgetExceeded

_USAGE = {"input_tokens": 900, "output_tokens": 400}

_GOOD_JSON = json.dumps(
    {
        "title": "3 errores al elegir empaques",
        "hook": "Estás pagando de más por esto",
        "scenes": [
            {"voice_text": "Primer error: ...", "broll_keywords": ["packaging", "restaurant"]},
            {"voice_text": "Segundo error: ...", "broll_keywords": ["takeaway"]},
        ],
        "caption": "Los 3 errores más comunes al elegir empaques para tu negocio.",
        "hashtags": ["empaques", "foodservice", "ecuador"],
        "cta": "Escríbenos para una asesoría",
    }
)


def _manifest():
    return parse_manifest(
        {
            "verops": "0.1",
            "package": {
                "name": "marketing-brain",
                "version": "0.1.0",
                "publisher": "ver-ia",
                "kind": "department",
            },
            "trust_tier": "first_party",
            "capabilities": [{"llm": {}}],
            "contracts": {"provides": ["WeeklyDeptReport@1"]},
            "events": {"emits": ["content.published@1"]},
            "autonomy": {"max_level": "L2", "default": "L0"},
            "compliance": {"pii": "none"},
        }
    )


@pytest.fixture
def dept(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-07"
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


def _brief():
    return ScriptBrief(
        tenant_id="tenant-a",
        pillar_id="tips-food-service",
        format="video",
        hook_type="dato",
        cta_type="contacto",
        time_slot="18:00-21:00",
        hypothesis=Hypothesis(
            question="¿Los tips de errores comunes generan más shares que los de beneficios?",
            metric="shares",
            success_criteria="> mediana del pilar",
            decision_if_true="duplicar formato errores-comunes",
            decision_if_false="volver a formato beneficios",
        ),
    )


def test_generate_package_feliz(dept):
    calls = []

    def fake_llm(system, messages):
        calls.append((system, messages))
        return _GOOD_JSON, _USAGE

    pkg = generate_package(dept, _brief(), load_profile("tiktok"), "MARCA X: empaques", llm_call=fake_llm)

    assert pkg.labels.pillar == "tips-food-service"
    assert pkg.generated_by == f"guionista@0.1:{MODEL}"
    assert len(pkg.scenes) == 2
    assert pkg.hashtags_master == ["empaques", "foodservice", "ecuador"]
    assert len(calls) == 1
    # contexto de marca y restricciones de red presentes en el prompt
    assert "MARCA X" in calls[0][0]
    assert "2200" in calls[0][0]
    # medibilidad: quedó en meter y en journal con correlation_id del package
    assert dept.meter.month_units("llm_tokens") == 1300
    entries = dept.journal.entries()
    assert entries[0]["correlation_id"] == pkg.package_id


def test_generate_package_reintenta_ante_json_invalido(dept):
    respuestas = iter(["esto no es json", _GOOD_JSON])
    intentos = []

    def fake_llm(system, messages):
        intentos.append(len(messages))
        return next(respuestas), _USAGE

    pkg = generate_package(dept, _brief(), load_profile("tiktok"), "ctx", llm_call=fake_llm)
    assert pkg.title.startswith("3 errores")
    assert len(intentos) == 2
    assert intentos[1] > intentos[0]  # el reintento lleva el error como feedback
    # ambos intentos medidos
    assert dept.meter.month_units("llm_tokens") == 2600


def test_generate_package_agota_reintentos(dept):
    def fake_llm(system, messages):
        return "nunca json", _USAGE

    with pytest.raises(RuntimeError, match="sin JSON válido"):
        generate_package(dept, _brief(), load_profile("tiktok"), "ctx", llm_call=fake_llm)


def test_presupuesto_bloquea_antes_de_llamar(dept):
    dept.meter.record("llm_tokens", qty=1, usd=9.99)
    llamadas = []

    def fake_llm(system, messages):
        llamadas.append(1)
        return _GOOD_JSON, _USAGE

    with pytest.raises(BudgetExceeded):
        generate_package(dept, _brief(), load_profile("tiktok"), "ctx", llm_call=fake_llm)
    assert llamadas == []  # el corte fue ANTES de gastar


def test_json_con_fences_se_extrae(dept):
    def fake_llm(system, messages):
        return f"```json\n{_GOOD_JSON}\n```", _USAGE

    pkg = generate_package(dept, _brief(), load_profile("tiktok"), "ctx", llm_call=fake_llm)
    assert pkg.cta == "Escríbenos para una asesoría"


def test_brand_context_del_tenant_real():
    ctx = load_brand_context("biodegradables")
    assert len(ctx) > 200  # company_context.md real


def test_brand_context_tenant_sin_archivo(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "marketing.yaml").write_text("content_pillars: []\n", encoding="utf-8")
    with pytest.raises(KeyError, match="brand_context_file"):
        load_brand_context("acme", base_dir=tmp_path)