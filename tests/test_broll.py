"""Tests de F1.4 — b-roll por escena (fetch inyectado, sin red)."""

import pytest

from marketing.broll import BrollError, fetch_broll_for_package
from org.kernel import Charter, Department, TenantStore

from tests.test_guionista import _manifest
from tests.test_tts import _package_con_escenas


@pytest.fixture
def dept(tmp_path):
    store = TenantStore("tenant-a", base_dir=tmp_path)
    charter = Charter(
        okrs=("okr",), budget_usd_month=10.0, approved_by="board@test", approved_at="2026-07-07"
    )
    yield Department(_manifest(), charter, store, granted_capabilities={"llm"})
    store.close()


def _fake_fetch_factory(log=None):
    counter = {"n": 0}

    def fake_fetch(query, out_dir, exclude_ids):
        counter["n"] += 1
        vid = f"vid-{counter['n']}"
        assert vid not in exclude_ids
        path = out_dir / f"pexels-{vid}.mp4"
        reused = path.exists()
        path.write_bytes(b"mp4")
        if log is not None:
            log.append(query)
        return path, vid, f"Pexels License · Autor {counter['n']}", reused

    return fake_fetch


def test_broll_un_clip_por_escena_con_atribucion(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    queries = []
    out = fetch_broll_for_package(dept, pkg, tmp_path / "broll", fetch_fn=_fake_fetch_factory(queries))

    videos = [a for a in out.assets if a.kind == "video"]
    assert len(videos) == len(pkg.scenes) == 2
    assert [v.scene_index for v in videos] == [0, 1]
    assert all(v.source.startswith("pexels:") for v in videos)
    assert all("Pexels License" in v.license_note for v in videos)
    # dedup: ids distintos por escena
    assert len({v.source for v in videos}) == 2
    # la primera escena usó sus keywords; la segunda (sin keywords) el pilar
    assert queries[0] == "eco"
    assert queries[1] == pkg.labels.pillar.replace("-", " ")
    # inmutabilidad + metering + journal
    assert not any(a.kind == "video" for a in pkg.assets)
    assert dept.meter.month_units("broll_clip") == 2
    assert any("b-roll asignado" in e["decision"] for e in dept.journal.entries())


def test_broll_no_se_repite(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    out = fetch_broll_for_package(dept, pkg, tmp_path, fetch_fn=_fake_fetch_factory())
    with pytest.raises(BrollError, match="ya tiene b-roll"):
        fetch_broll_for_package(dept, out, tmp_path, fetch_fn=_fake_fetch_factory())


def test_broll_fallback_al_pilar(dept, tmp_path):
    pkg = _package_con_escenas(dept)
    queries = []

    def fetch_solo_pilar(query, out_dir, exclude_ids):
        queries.append(query)
        if query != pkg.labels.pillar.replace("-", " "):
            raise BrollError("sin resultados")
        path = out_dir / f"pexels-p{len(queries)}.mp4"
        path.write_bytes(b"mp4")
        return path, f"p{len(queries)}", "Pexels License · X", False

    out = fetch_broll_for_package(dept, pkg, tmp_path, fetch_fn=fetch_solo_pilar)
    assert len([a for a in out.assets if a.kind == "video"]) == 2
    # escena 0 intentó sus keywords y cayó al fallback
    assert queries[0] == "eco"
    assert queries[1] == pkg.labels.pillar.replace("-", " ")


def test_pick_file_evita_uhd_y_prefiere_1920():
    from marketing.broll import _pick_file

    video = {
        "video_files": [
            {"width": 2160, "height": 3840, "link": "uhd"},
            {"width": 1080, "height": 1920, "link": "fhd"},
            {"width": 720, "height": 1280, "link": "hd"},
            {"width": 1920, "height": 1080, "link": "horizontal"},
        ]
    }
    assert _pick_file(video)["link"] == "fhd"
    # si solo hay UHD vertical, se usa (mejor que nada)
    solo_uhd = {"video_files": [{"width": 2160, "height": 3840, "link": "uhd"}]}
    assert _pick_file(solo_uhd)["link"] == "uhd"


def test_broll_sin_escenas(dept, tmp_path):
    # model_copy no re-valida: sirve para simular un package sin escenas
    pkg = _package_con_escenas(dept).model_copy(update={"scenes": []})
    with pytest.raises(BrollError, match="sin escenas"):
        fetch_broll_for_package(dept, pkg, tmp_path, fetch_fn=_fake_fetch_factory())
