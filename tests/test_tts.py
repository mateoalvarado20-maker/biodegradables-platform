"""Tests de F1.3 — TTS con word boundaries (backend inyectado, sin red)."""

import pytest

from marketing.brand import load_tts_voice
from marketing.models import Hypothesis, WordTiming
from marketing.tts import TtsError, synthesize_package
from org.kernel import Charter, Department, TenantStore, parse_manifest

from tests.test_guionista import _brief, _manifest  # reutiliza fixtures de dominio


@pytest.fixture
def dept(tmp_path):
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-07"
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


def _fake_synth(text: str, voice: str, out_path) -> list[tuple[str, float, float]]:
    out_path.write_bytes(b"mp3-fake")
    words = text.split()
    return [(w, i * 300.0, i * 300.0 + 250.0) for i, w in enumerate(words)]


def _package_con_escenas(dept):
    import json

    from marketing.guionista import generate_package
    from marketing.profiles import load_profile

    good = json.dumps(
        {
            "title": "Título de prueba",
            "hook": "Hook de prueba",
            "scenes": [
                {"voice_text": "hola mundo sostenible", "broll_keywords": ["eco"]},
                {"voice_text": "segunda escena corta", "broll_keywords": []},
            ],
            "caption": "caption",
            "hashtags": ["eco"],
            "cta": "escríbenos ya",
        }
    )
    return generate_package(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        llm_call=lambda s, m: (good, {"input_tokens": 10, "output_tokens": 10}),
    )


def test_sintesis_persiste_audio_y_timings(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    out = synthesize_package(dept, pkg, "es-EC-AndreaNeural", tmp_path / "voz", synth_fn=_fake_synth)

    audios = [a for a in out.assets if a.kind == "audio"]
    assert len(audios) == 2
    assert all(a.source == "tts:azure:es-EC-AndreaNeural@0.1" for a in audios)
    # 3 palabras + 3 palabras, con escena correcta y orden temporal
    assert len(out.word_timings) == 6
    assert {t.scene_index for t in out.word_timings} == {0, 1}
    escena0 = [t for t in out.word_timings if t.scene_index == 0]
    assert [t.word for t in escena0] == ["hola", "mundo", "sostenible"]
    assert all(t.end_ms > t.start_ms for t in out.word_timings)
    # inmutabilidad: el package original no fue tocado
    assert pkg.word_timings == [] and len(pkg.assets) == 0
    # metering y auditoría
    assert dept.meter.month_units("tts_chars") == len("hola mundo sostenible") + len(
        "segunda escena corta"
    )
    assert any("voz sintetizada" in e["decision"] for e in dept.journal.entries())


def test_sintesis_rechaza_package_sin_escenas(dept, tmp_path):
    # un package de carrusel puede no tener escenas — para TTS es un error
    from marketing.models import ContentPackage, ExperimentLabels

    carousel = ContentPackage(
        package_id="pkg-carousel-01",
        tenant_id="tenant-a",
        labels=ExperimentLabels(
            pillar="tips-food-service",
            hook_type="lista",
            format="carousel",
            time_slot="18:00-21:00",
            cta_type="contacto",
        ),
        hypothesis=Hypothesis(
            question="¿pregunta suficientemente larga?",
            metric="views",
            success_criteria="> mediana",
            decision_if_true="reforzar",
            decision_if_false="descartar",
        ),
        title="Título",
        hook="Hook",
        caption_master="c",
        cta="cta",
        created_at="2026-07-07",
    )
    with pytest.raises(TtsError, match="sin escenas"):
        synthesize_package(dept, carousel, "voz", tmp_path, synth_fn=_fake_synth)


def test_sintesis_no_se_repite(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    out = synthesize_package(dept, pkg, "voz-x", tmp_path, synth_fn=_fake_synth)
    with pytest.raises(TtsError, match="ya tiene voz"):
        synthesize_package(dept, out, "voz-x", tmp_path, synth_fn=_fake_synth)


def test_backend_sin_boundaries_es_error(dept, tmp_path):
    pkg = _package_con_escenas(dept)

    def synth_mudo(text, voice, out_path):
        out_path.write_bytes(b"mp3")
        return []

    with pytest.raises(TtsError, match="word boundaries"):
        synthesize_package(dept, pkg, "voz", tmp_path, synth_fn=synth_mudo)


def test_word_timing_valida_rango():
    with pytest.raises(ValueError):
        WordTiming(scene_index=0, word="hola", start_ms=100.0, end_ms=100.0)


def test_voz_del_tenant_real():
    assert load_tts_voice("biodegradables") == "es-EC-AndreaNeural"


def test_voz_faltante(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "marketing.yaml").write_text("content_pillars: []\n", encoding="utf-8")
    with pytest.raises(KeyError, match="tts_voice"):
        load_tts_voice("acme", base_dir=tmp_path)
