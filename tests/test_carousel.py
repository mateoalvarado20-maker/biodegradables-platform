"""Tests de F1.6 — carruseles (runner inyectado) + telemetría (directriz #14)."""

import json
from pathlib import Path

import pytest

from marketing.carousel import render_carousel
from marketing.guionista import generate_package
from marketing.models import Slide
from marketing.profiles import load_profile
from marketing.render_video import RENDER_DIR, RenderError
from marketing.telemetry import stage_stats
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _brief, _manifest

_CAROUSEL_JSON = json.dumps(
    {
        "title": "5 datos de empaques sostenibles",
        "hook": "El 5 te va a sorprender",
        "slides": [
            {"title": "5 datos que no sabías", "body": "Sobre empaques sostenibles en Ecuador"},
            {"title": "1. Bagazo de caña", "body": "Aguanta líquidos calientes sin deformarse"},
            {"title": "2. Compostable ≠ reciclable", "body": "Son procesos distintos con destinos distintos"},
            {"title": "3. El costo real", "body": "La diferencia por unidad es menor de lo que crees"},
            {"title": "Escríbenos", "body": "Te asesoramos gratis para elegir bien"},
        ],
        "caption": "Los 5 datos que todo negocio debería saber.",
        "hashtags": ["sostenibilidad", "ecuador"],
        "cta": "Escríbenos para una asesoría gratis",
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


def _carousel_pkg(dept):
    brief = _brief().model_copy(update={"format": "carousel"})
    return generate_package(
        dept,
        brief,
        load_profile("tiktok"),
        "ctx",
        llm_call=lambda s, m: (_CAROUSEL_JSON, {"input_tokens": 10, "output_tokens": 10}),
    )


def _fake_runner(calls):
    def runner(args):
        calls.append(args)
        Path(args[3]).write_bytes(b"p" * 10_000)

    return runner


def test_guionista_carousel_produce_slides(dept):
    pkg = _carousel_pkg(dept)
    assert pkg.labels.format == "carousel"
    assert len(pkg.slides) == 5
    assert pkg.scenes == []
    assert isinstance(pkg.slides[0], Slide)


def test_render_carousel_un_png_por_slide(dept, tmp_path):
    pkg = _carousel_pkg(dept)
    calls = []
    out = render_carousel(dept, pkg, tmp_path / "slides", brand_name="Marca X", runner=_fake_runner(calls))

    imgs = [a for a in out.assets if a.kind == "image"]
    assert len(imgs) == 5
    assert [a.scene_index for a in imgs] == [0, 1, 2, 3, 4]
    assert out.status == "produced"
    # un still por slide, con --frame correcto
    frames = [c[-1] for c in calls]
    assert frames == [f"--frame={i}" for i in range(5)]
    # props escritos con brand y cta
    props = json.loads(
        (RENDER_DIR / "public" / pkg.package_id / "carousel-props.json").read_text(encoding="utf-8")
    )
    assert props["brand_name"] == "Marca X"
    assert props["cta"] == pkg.cta
    assert dept.meter.month_units("carousel_render") == 1


def test_render_carousel_guards(dept, tmp_path):
    video_pkg = generate_package(
        dept,
        _brief(),
        load_profile("tiktok"),
        "ctx",
        llm_call=lambda s, m: (
            json.dumps(
                {
                    "title": "Título",
                    "hook": "Hook",
                    "scenes": [{"voice_text": "hola", "broll_keywords": []}],
                    "caption": "c",
                    "hashtags": [],
                    "cta": "cta",
                }
            ),
            {"input_tokens": 1, "output_tokens": 1},
        ),
    )
    with pytest.raises(RenderError, match="no es carrusel"):
        render_carousel(dept, video_pkg, tmp_path, runner=_fake_runner([]))

    pkg = _carousel_pkg(dept)
    out = render_carousel(dept, pkg, tmp_path, runner=_fake_runner([]))
    with pytest.raises(RenderError, match="ya tiene slides"):
        render_carousel(dept, out, tmp_path, runner=_fake_runner([]))


def test_telemetria_registra_etapas(dept, tmp_path):
    pkg = _carousel_pkg(dept)
    render_carousel(dept, pkg, tmp_path, runner=_fake_runner([]))
    stats = stage_stats(dept)
    assert "guion" in stats and "carousel" in stats
    assert stats["guion"]["runs"] == 1
    assert stats["guion"]["tokens"] == 20
    assert stats["carousel"]["slides"] == 5
    assert stats["carousel"]["avg_ms"] >= 0
