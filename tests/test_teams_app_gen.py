"""Tests F5.1 (VER-IA 2026-07-04): generador de apps de Teams por tenant.

El manifest, los iconos (con el color de marca) y el zip listo para subir
salen de tenants/<slug>/config.yaml — cero edición manual por cliente.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GUID = "12345678-abcd-4ef0-9876-0123456789ab"
GUID2 = "87654321-dcba-4f0e-6789-ba9876543210"


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location(
        "gen_teams_app", ROOT / "ops" / "gen_teams_app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_genera_los_dos_zips_para_andex(gen, tmp_path):
    zips = gen.generar("andex", GUID, GUID2, tmp_path)
    assert [z.name for z in zips] == [
        "andex_teams_data.zip", "andex_teams_activities.zip",
    ]
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            assert set(zf.namelist()) == {"manifest.json", "color.png", "outline.png"}


def test_manifest_interpolado_desde_config(gen, tmp_path):
    zips = gen.generar("andex", GUID, GUID2, tmp_path)
    with zipfile.ZipFile(zips[0]) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert m["id"] == GUID
    assert m["bots"][0]["botId"] == GUID
    assert "Andex" in m["name"]["short"]
    assert len(m["name"]["short"]) <= 30          # límite de Teams
    assert m["accentColor"] == "#0B6E99"           # branding del YAML de andex
    assert m["developer"]["name"] == "Andex"
    assert m["manifestVersion"] == "1.17"


def test_iconos_con_tamanos_de_teams(gen, tmp_path):
    from PIL import Image
    zips = gen.generar("andex", GUID, GUID2, tmp_path)
    with zipfile.ZipFile(zips[0]) as zf:
        color = Image.open(io.BytesIO(zf.read("color.png")))
        outline = Image.open(io.BytesIO(zf.read("outline.png")))
    assert color.size == (192, 192)
    assert outline.size == (32, 32)
    # El icono de color usa el color de marca del tenant (#0B6E99)
    px = color.convert("RGBA").getpixel((96, 20))
    assert px[:3] == (0x0B, 0x6E, 0x99)


def test_sin_app_id_deja_placeholder(gen, tmp_path):
    zips = gen.generar("andex", None, None, tmp_path)
    with zipfile.ZipFile(zips[0]) as zf:
        m = json.loads(zf.read("manifest.json"))
    assert m["id"].startswith("REEMPLAZAR_CON_")


def test_guid_invalido_falla_claro(gen, tmp_path):
    with pytest.raises(SystemExit, match="App ID inválido"):
        gen.generar("andex", "no-es-un-guid", GUID2, tmp_path)
