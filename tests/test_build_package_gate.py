"""Test del gate de completitud de imports del paquete de deploy (F4).

Incidente 2026-07-03: ask_agent ganó `import llm_usage` (F3) pero
tools/build_bot_package.py tiene una lista manual de archivos — el zip se
deployó sin llm_usage.py y la primera consulta al bot habría crasheado con
ModuleNotFoundError. Este gate analiza los imports (AST, incluye lazy
imports dentro de funciones) de cada módulo empaquetado y falla el build si
alguno importa un módulo raíz que no viaja en el zip.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def builder():
    spec = importlib.util.spec_from_file_location(
        "build_bot_package", ROOT / "tools" / "build_bot_package.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_paquete_actual_sin_imports_faltantes(builder):
    """La lista BOT_FILES real cubre todo lo que sus módulos importan."""
    assert builder._check_imports() == []


def test_gate_detecta_modulo_faltante(builder, monkeypatch):
    """Reproduce el incidente: quitar llm_usage.py de la lista lo detecta
    (el import vive DENTRO de una función de ask_agent — lazy import)."""
    sin_llm = [f for f in builder.BOT_FILES if f != "llm_usage.py"]
    monkeypatch.setattr(builder, "BOT_FILES", sin_llm)
    errores = builder._check_imports()
    assert any("llm_usage.py" in e and "ask_agent" in e for e in errores), errores


def test_gate_detecta_import_toplevel_faltante(builder, monkeypatch):
    sin_ledger = [f for f in builder.BOT_FILES if f != "send_ledger.py"]
    monkeypatch.setattr(builder, "BOT_FILES", sin_ledger)
    errores = builder._check_imports()
    assert any("send_ledger.py" in e for e in errores), errores


def test_paquetes_de_directorio_no_se_flaggean(builder):
    """core/, connectors/, tenants/ viajan como directorios — el import
    perezoso `from core.config.loader import ...` de core_config no debe
    reportarse como faltante."""
    errores = builder._check_imports()
    assert not any(e.startswith("core") for e in errores)
