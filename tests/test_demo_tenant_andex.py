"""Garantía anti-fuga del tenant DEMO Andex (Fase 1).

Carga el tenant ficticio `andex` (TENANT_CONFIG_SOURCE=yaml + TENANT_SLUG=andex)
y verifica que los system prompts de los bots NO contengan NINGÚN identificador
del cliente real. Es la red que asegura que el demo es seguro de mostrar.

Restaura core_config/ask_agent a legacy al final para no contaminar otros tests.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def andex(tmp_path, monkeypatch):
    """core_config + ask_agent recargados sobre el tenant Andex (yaml)."""
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "KNOWN_COLLABORATORS",
        "andres:amora@andexdemo.com,carolina:cvega@andexdemo.com,"
        "info:info@andexdemo.com,quito:quito@andexdemo.com",
    )
    import core_config
    importlib.reload(core_config)
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    try:
        yield ask_agent
    finally:
        # Volver a legacy para los demás tests.
        monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
        importlib.reload(core_config)
        importlib.reload(ask_agent)


def test_andex_identity_loaded(andex):
    import core_config as c
    assert c.COMPANY_NAME == "Andex"
    assert c.gerente_general_name() == "Roberto Salinas"
    assert c.SUPERVISORS_ONLY_EMAILS == {"rsalinas@andexdemo.com"}
    assert c.ASISTENTE_EMAILS == {"info@andexdemo.com", "quito@andexdemo.com"}


def test_bot_prompts_have_no_real_data(andex):
    """Los prompts del Data Bot y del Activities Bot deben salir limpios."""
    import demo_guard
    prompts = {
        "data": andex._system_prompt_data(),
        "activities": andex._system_prompt_activities("rsalinas@andexdemo.com"),
        "activities_noid": andex._system_prompt_activities(
            "unidentified-abc123@andexdemo.com"
        ),
    }
    for label, txt in prompts.items():
        assert demo_guard.scan_for_real_data(txt) == [], f"fuga en prompt {label}"
    # Y debe traer la identidad ficticia correcta.
    assert "Andex" in prompts["data"]
    assert "Roberto Salinas" in prompts["data"]  # supervisor en ejemplos


def test_verify_demo_config_ok_for_andex(monkeypatch):
    import demo_guard
    monkeypatch.setenv("DEMO_MODE", "1")
    monkeypatch.setenv("TENANT_SLUG", "andex")
    monkeypatch.setenv("DEMO_EMAIL_DOMAIN", "andexdemo.com")
    monkeypatch.setenv("DEMO_EMAIL_TO", "demo@andexdemo.com")
    monkeypatch.setenv("DEMO_FROM_USER", "amora@andexdemo.com")
    demo_guard.verify_demo_config()  # no levanta
