"""Tests de F2.0 — ciclo de reparación + FPY (LLMs inyectados, sin red)."""

import json

import pytest

from marketing.gate import copy_checks, review_copy
from marketing.profiles import load_profile
from marketing.repair import generate_with_repair
from marketing.telemetry import fpy_stats
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _brief, _manifest

HARD_RULES = {"claims_prohibidos": ["cero impacto ambiental"]}

_SCENES_65_PALABRAS = [
    {"voice_text": " ".join(["palabra"] * 13), "broll_keywords": ["eco"]} for _ in range(5)
]


def _guion(caption="Caption limpio del guion.", hook="Hook potente y claro", scenes=None):
    return json.dumps(
        {
            "title": "Título de prueba válido",
            "hook": hook,
            "scenes": scenes if scenes is not None else _SCENES_65_PALABRAS,
            "caption": caption,
            "hashtags": ["eco", "ecuador"],
            "cta": "Escríbenos hoy",
        }
    )


def _review_json(score, blockers=None):
    return json.dumps(
        {
            "score": score,
            "blockers": blockers if blockers is not None else ([] if score >= 75 else ["defecto de prueba"]),
            "improvements": ["nota opcional"],
            "claim_issues": [],
        }
    )


@pytest.fixture
def dept(tmp_path, monkeypatch):
    import llm_usage

    monkeypatch.setattr(llm_usage, "USAGE_PATH", tmp_path / "llm_usage.json")
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",),
        budget_usd_month=10.0,
        approved_by="board@test",
        approved_at="2026-07-09",
        hard_rules=HARD_RULES,
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


_USAGE = {"input_tokens": 100, "output_tokens": 50}


# --- checks de copy en borrador ---------------------------------------------------


def test_copy_checks_detecta_emojis_y_duracion(dept):
    from marketing.guionista import generate_package

    corto_con_emoji = generate_package(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        llm_call=lambda s, m: (
            _guion(caption="Caption con emoji 🌱 prohibido", scenes=[{"voice_text": "muy corto"}]),
            _USAGE,
        ),
    )
    problems = copy_checks(corto_con_emoji, load_profile("tiktok"), HARD_RULES)
    assert any("emojis" in p for p in problems)
    assert any("duración estimada" in p for p in problems)


def test_review_copy_determinista_no_gasta_llm(dept):
    from marketing.guionista import generate_package

    pkg = generate_package(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        llm_call=lambda s, m: (_guion(caption="Con claim de cero impacto ambiental"), _USAGE),
    )
    llamadas = []
    v = review_copy(dept, pkg, load_profile("tiktok"), "ctx", llm_call=lambda s, m: llamadas.append(1))
    assert v.approved is False and v.deterministic is True
    assert llamadas == []
    assert any("claim prohibido" in r for r in v.reasons)


# --- ciclo de reparación ------------------------------------------------------------


def test_aprobada_al_primer_intento_cuenta_fpy(dept):
    result = generate_with_repair(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        gen_llm_call=lambda s, m: (_guion(), _USAGE),
        review_llm_call=lambda s, m: (_review_json(90), _USAGE),
    )
    assert result.approved and result.first_pass
    assert result.package.status == "copy_approved"
    assert len(result.attempts) == 1
    stats = fpy_stats(dept)
    assert stats["pieces"] == 1 and stats["fpy"] == 1.0


def test_reparacion_exitosa_al_segundo_intento(dept):
    gen_calls = {"n": 0}

    def gen_llm(system, messages):
        gen_calls["n"] += 1
        if gen_calls["n"] == 1:
            return _guion(caption="Caption con emoji ✨"), _USAGE
        # la reparación recibe el feedback y devuelve JSON + cambios
        assert "RECHAZÓ" in messages[0]["content"]
        assert "emoji" in messages[0]["content"]
        data = json.loads(_guion())
        data["cambios_realizados"] = ["eliminé el emoji del caption"]
        return json.dumps(data), _USAGE

    result = generate_with_repair(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        gen_llm_call=gen_llm,
        review_llm_call=lambda s, m: (_review_json(85), _USAGE),
    )
    assert result.approved and not result.first_pass
    assert len(result.attempts) == 2
    # intento 1: rechazo determinista por emoji, sin cambios previos
    assert result.attempts[0].approved is False
    assert result.attempts[0].cambios == []
    # intento 2: aprobado, con los cambios de la reparación registrados
    assert result.attempts[1].approved is True
    assert result.attempts[1].cambios == ["eliminé el emoji del caption"]
    # registro por intento en journal (motivo/cambios/costo/tiempo)
    entries = dept.journal.entries()
    textos = " ".join(e["decision"] for e in entries)
    assert "intento 1" in textos and "intento 2" in textos
    refs = " ".join(r for e in entries for r in e["context_refs"])
    assert "cambio aplicado" in refs and "costo intento" in refs
    stats = fpy_stats(dept)
    assert stats["fpy"] == 0.0 and stats["approved_after_repair"] == 1
    assert stats["repair_success_rate"] == 1.0
    assert "emojis" in stats["error_categories"]


def test_rechazo_definitivo_tras_max_intentos(dept):
    def gen_llm(system, messages):
        data = json.loads(_guion())
        if "RECHAZÓ" in messages[0]["content"]:
            data["cambios_realizados"] = ["intenté corregir"]
        return json.dumps(data), _USAGE

    result = generate_with_repair(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        gen_llm_call=gen_llm,
        review_llm_call=lambda s, m: (_review_json(50, blockers=["hook débil"]), _USAGE),
    )
    assert result.approved is False
    assert result.package.status == "qa_rejected"
    assert len(result.attempts) == 3  # 1 original + 2 reparaciones (máx del board)
    entries = dept.journal.entries()
    assert any("rechazo DEFINITIVO" in e["decision"] for e in entries)
    stats = fpy_stats(dept)
    assert stats["rejected_final"] == 1 and stats["repair_success_rate"] == 0.0


def test_reparacion_preserva_identidad_experimental(dept):
    def gen_llm(system, messages):
        if "RECHAZÓ" in messages[0]["content"]:
            data = json.loads(_guion())
            data["cambios_realizados"] = ["fix"]
            return json.dumps(data), _USAGE
        return _guion(caption="Caption con emoji 🌱"), _USAGE

    result = generate_with_repair(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        gen_llm_call=gen_llm,
        review_llm_call=lambda s, m: (_review_json(88), _USAGE),
    )
    # mismo package_id, misma hipótesis, mismos labels; generated_by marca la reparación
    assert result.approved
    assert result.package.hypothesis == _brief().hypothesis
    assert "+repair@" in result.package.generated_by