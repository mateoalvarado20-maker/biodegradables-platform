"""Tests de F1.5 — render (runner inyectado: sin Node ni Chrome en CI)."""

import json
from pathlib import Path

import pytest

from marketing.broll import fetch_broll_for_package
from marketing.render_video import RENDER_DIR, RenderError, build_props, render_package
from marketing.tts import synthesize_package
from org.kernel import Charter, Department, TenantStore

from tests.test_broll import _fake_fetch_factory
from tests.test_guionista import _manifest
from tests.test_tts import _fake_synth, _package_con_escenas


@pytest.fixture
def dept(tmp_path):
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-07"
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


@pytest.fixture
def pkg_completo(dept, tmp_path):
    """package con guion + voz + b-roll (todo fake, sin red)."""
    pkg = _package_con_escenas(dept)
    pkg = synthesize_package(dept, pkg, "voz-test", tmp_path / "voz", synth_fn=_fake_synth)
    return fetch_broll_for_package(dept, pkg, tmp_path / "broll", fetch_fn=_fake_fetch_factory())


def _fake_runner_factory(calls):
    def fake_runner(args):
        calls.append(args)
        # el runner real escribe el archivo de salida (mp4 o jpg)
        out = Path(args[3])
        out.write_bytes(b"x" * 200_000 if out.suffix == ".mp4" else b"jpg")

    return fake_runner


def test_build_props_deriva_duraciones_de_los_timings(pkg_completo):
    props = build_props(pkg_completo, "#123456")
    assert len(props["scenes"]) == 2
    # escena 0: 3 palabras fake → última termina en 850ms → clamp a MIN_SCENE_MS
    assert props["scenes"][0]["duration_ms"] == 1500.0
    assert props["scenes"][0]["video_duration_ms"] == 8000.0  # del fake fetch (Loop)
    assert props["scenes"][0]["audio"].endswith("scene00.mp3")
    assert [w["word"] for w in props["scenes"][0]["words"]] == ["hola", "mundo", "sostenible"]
    assert props["brand_color"] == "#123456"


def test_build_props_exige_pipeline_completo(dept, tmp_path):
    solo_guion = _package_con_escenas(dept)
    with pytest.raises(RenderError, match="sin audio TTS"):
        build_props(solo_guion, "#000000")
    con_voz = synthesize_package(dept, solo_guion, "v", tmp_path, synth_fn=_fake_synth)
    with pytest.raises(RenderError, match="sin b-roll"):
        build_props(con_voz, "#000000")


def test_render_package_feliz(dept, pkg_completo, tmp_path):
    calls = []
    out = render_package(
        dept, pkg_completo, tmp_path / "final", runner=_fake_runner_factory(calls)
    )

    assert out.status == "produced"
    finales = [a for a in out.assets if a.scene_index is None and a.kind in ("video", "cover")]
    assert {a.kind for a in finales} == {"video", "cover"}
    assert all(Path(a.path).exists() for a in finales)
    # invocó render + still
    assert [c[0] for c in calls] == ["render", "still"]
    # staging en public/ + props.json escritos
    staged = RENDER_DIR / "public" / pkg_completo.package_id
    assert (staged / "scene00.mp3").exists() and (staged / "scene01.mp4").exists()
    props = json.loads((staged / "props.json").read_text(encoding="utf-8"))
    assert len(props["scenes"]) == 2
    # metering + journal + inmutabilidad
    assert dept.meter.month_units("render") == 1
    assert any("video renderizado" in e["decision"] for e in dept.journal.entries())
    assert pkg_completo.status == "draft"


def test_render_no_se_repite(dept, pkg_completo, tmp_path):
    out = render_package(dept, pkg_completo, tmp_path, runner=_fake_runner_factory([]))
    with pytest.raises(RenderError, match="ya tiene render"):
        render_package(dept, out, tmp_path, runner=_fake_runner_factory([]))


def test_qa_rechaza_mp4_enano(dept, pkg_completo, tmp_path):
    def runner_enano(args):
        Path(args[3]).write_bytes(b"x")  # 1 byte

    with pytest.raises(RenderError, match="QA"):
        render_package(dept, pkg_completo, tmp_path, runner=runner_enano)
