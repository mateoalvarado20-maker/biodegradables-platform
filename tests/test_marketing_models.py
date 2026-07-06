"""Tests de F1.1: modelos de contenido, perfil de plataforma TikTok y pilares
como hipótesis (ROADMAP.md F1)."""

import pytest
from pydantic import ValidationError

from marketing.models import (
    ContentPackage,
    ExperimentLabels,
    Pillar,
    PlatformRendition,
    Scene,
)
from marketing.pillars import active_pillars, load_pillars
from marketing.profiles import available_platforms, load_profile


def _labels(**over):
    data = {
        "pillar": "producto-en-uso",
        "hook_type": "pregunta",
        "format": "video",
        "time_slot": "18:00-21:00",
        "cta_type": "visita-web",
    }
    data.update(over)
    return ExperimentLabels(**data)


def _package(**over):
    data = {
        "package_id": "pkg-2026-07-06-001",
        "tenant_id": "biodegradables",
        "labels": _labels(),
        "title": "¿Sabías esto de los empaques?",
        "hook": "El 90% no sabe esto",
        "scenes": [Scene(voice_text="hola", broll_keywords=["packaging"])],
        "caption_master": "Descubre por qué...",
        "hashtags_master": ["sostenibilidad", "ecuador"],
        "cta": "Visita el link",
        "created_at": "2026-07-06T10:00:00-05:00",
    }
    data.update(over)
    return ContentPackage(**data)


def _rendition(**over):
    data = {
        "package_id": "pkg-2026-07-06-001",
        "platform": "tiktok",
        "format": "video",
        "caption": "Descubre por qué... #sostenibilidad",
        "hashtags": ["sostenibilidad", "ecuador"],
        "duration_s": 32.5,
        "width": 1080,
        "height": 1920,
        "media_paths": ["out/video.mp4"],
        "cover_path": "out/cover.jpg",
    }
    data.update(over)
    return PlatformRendition(**data)


# --- perfil TikTok -------------------------------------------------------------


def test_perfil_tiktok_carga_y_es_sano():
    p = load_profile("tiktok")
    assert p.platform == "tiktok"
    assert "video" in p.formats and "carousel" in p.formats
    assert "story" not in p.formats  # sin API — flujo asistido, no rendition
    assert (p.width, p.height) == (1080, 1920)
    assert p.platform_cap_posts_per_day == 15
    assert "tiktok" in available_platforms()


def test_perfil_desconocido():
    with pytest.raises(KeyError):
        load_profile("myspace")


def test_rendition_valida_contra_perfil():
    p = load_profile("tiktok")
    assert p.validate_rendition(_rendition()) == []


def test_rendition_violaciones_detalladas():
    p = load_profile("tiktok")
    mala = _rendition(
        caption="x" * 3000,
        hashtags=["a", "b", "c", "d", "e", "f"],
        duration_s=700.0,
        width=720,
        height=1280,
    )
    problems = " | ".join(p.validate_rendition(mala))
    assert "caption" in problems
    assert "hashtags" in problems
    assert "duración" in problems
    assert "resolución" in problems


def test_rendition_carrusel_slides():
    p = load_profile("tiktok")
    ok = _rendition(format="carousel", duration_s=None, media_paths=[f"s{i}.png" for i in range(6)])
    assert p.validate_rendition(ok) == []
    exceso = _rendition(
        format="carousel", duration_s=None, media_paths=[f"s{i}.png" for i in range(40)]
    )
    assert any("slides" in x for x in p.validate_rendition(exceso))


def test_rendition_formato_no_soportado():
    p = load_profile("tiktok")
    story = _rendition(format="story", duration_s=10.0)
    assert any("no soportado" in x for x in p.validate_rendition(story))


# --- ContentPackage -------------------------------------------------------------


def test_package_valido():
    pkg = _package()
    assert pkg.status == "draft"
    assert pkg.labels.pillar == "producto-en-uso"


def test_package_sin_labels_no_existe():
    with pytest.raises(ValidationError):
        ContentPackage(
            package_id="pkg-sin-labels-1",
            tenant_id="biodegradables",
            title="Título válido",
            hook="Hook válido",
            caption_master="x",
            cta="ver más",
            created_at="2026-07-06",
        )


def test_package_video_exige_escenas():
    with pytest.raises(ValidationError, match="escena"):
        _package(scenes=[])


def test_package_hashtags_normalizados():
    with pytest.raises(ValidationError, match="sin '#'"):
        _package(hashtags_master=["#sostenibilidad"])


def test_labels_time_slot_formato():
    with pytest.raises(ValidationError):
        _labels(time_slot="por la noche")


def test_modelos_estrictos_rechazan_campos_extra():
    with pytest.raises(ValidationError):
        Scene(voice_text="hola", campo_inventado=1)


# --- pilares como hipótesis -------------------------------------------------------


def test_pilares_del_tenant_cargan_como_hipotesis():
    pillars = load_pillars("biodegradables")
    assert len(pillars) == 5
    assert {p.status for p in pillars} == {"hypothesis"}
    ids = {p.id for p in pillars}
    assert "producto-en-uso" in ids and "tendencias-eco-ecuador" in ids
    assert active_pillars("biodegradables") == pillars  # ninguno retirado aún


def test_pilar_validated_exige_evidencia():
    with pytest.raises(ValidationError, match="evidence"):
        Pillar(id="x-pilar", name="Pilar X", status="validated")
    ok = Pillar(id="x-pilar", name="Pilar X", status="validated", evidence=["exp-001"])
    assert ok.evidence == ["exp-001"]


def test_pilares_duplicados_rechazados(tmp_path):
    tdir = tmp_path / "acme"
    tdir.mkdir()
    (tdir / "marketing.yaml").write_text(
        "content_pillars:\n"
        "  - {id: uno, name: Pilar Uno}\n"
        "  - {id: uno, name: Pilar Uno Bis}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicados"):
        load_pillars("acme", base_dir=tmp_path)


def test_tenant_sin_marketing_yaml(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_pillars("inexistente", base_dir=tmp_path)
