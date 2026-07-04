"""Tests F4.3 (VER-IA 2026-07-03): prospección outbound migrada al bot.

reply_agent_tick (cada 15 min, antes timer de azfunc) corre en el bot bajo
el módulo `prospecting` + su flag de cutover. Doble gate igual que
logistics: módulo del catálogo Y flag de ventana de cutover.

(El notificador de secuencias Apollo se retiró el 2026-07-04 por pedido del
dueño — nunca se activó en el bot; ver archive/README.md.)
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
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.delenv("REPLY_AGENT_IN_BOT", raising=False)
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    importlib.reload(send_ledger)
    import core_config
    importlib.reload(core_config)
    import teams_bot
    return importlib.reload(teams_bot)


def _ids(bot) -> set[str]:
    bot._schedule_jobs()
    return {j.id for j in bot.scheduler.get_jobs()}


def test_prospecting_en_catalogo():
    import core_config
    importlib.reload(core_config)
    from core.config.schema import KNOWN_MODULES
    assert "prospecting" in core_config.MODULES
    assert "prospecting" in KNOWN_MODULES


def test_sin_flag_no_se_registra(bot):
    """Flag de cutover apagado → el job NO corre en el bot."""
    assert "reply_agent_tick" not in _ids(bot)


def test_con_flag_se_registra(bot, monkeypatch):
    monkeypatch.setenv("REPLY_AGENT_IN_BOT", "1")
    assert "reply_agent_tick" in _ids(bot)


def test_modulo_apagado_gana_al_flag(bot, monkeypatch):
    """Un tenant SIN prospección contratada no corre el job aunque el flag
    quede seteado por error."""
    import core_config
    monkeypatch.setenv("REPLY_AGENT_IN_BOT", "1")
    monkeypatch.setitem(core_config.MODULES, "prospecting", False)
    assert "reply_agent_tick" not in _ids(bot)


def test_apollo_notifier_retirado(bot, monkeypatch):
    """2026-07-04: el notificador de secuencias Apollo se retiró por pedido
    del dueño. Ni con su flag viejo seteado debe registrarse, el módulo ya no
    vive en la raíz, y el código quedó preservado en archive/."""
    monkeypatch.setenv("APOLLO_NOTIFIER_IN_BOT", "1")
    monkeypatch.setenv("REPLY_AGENT_IN_BOT", "1")
    assert "apollo_notifier_tick" not in _ids(bot)
    assert not (ROOT / "apollo_completion_notifier.py").exists()
    assert (ROOT / "archive" / "apollo_completion_notifier.py").exists()
