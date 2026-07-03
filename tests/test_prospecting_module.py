"""Tests F4.3 (VER-IA 2026-07-03): prospección outbound migrada al bot.

reply_agent_tick (cada 15 min, antes timer de azfunc) y apollo_notifier_tick
(cada 2h, antes schtask en la PC de Mateo) corren en el bot bajo el módulo
`prospecting` + sus flags de cutover. Doble gate igual que logistics:
módulo del catálogo Y flag de ventana de cutover.
"""
from __future__ import annotations

import importlib
import os
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
    monkeypatch.delenv("APOLLO_NOTIFIER_IN_BOT", raising=False)
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


def test_sin_flags_no_se_registran(bot):
    """Default: los flags de cutover apagados → los jobs NO corren en el bot
    (siguen en azfunc/PC hasta la ventana de cutover)."""
    ids = _ids(bot)
    assert "reply_agent_tick" not in ids
    assert "apollo_notifier_tick" not in ids


def test_con_flags_se_registran(bot, monkeypatch):
    monkeypatch.setenv("REPLY_AGENT_IN_BOT", "1")
    monkeypatch.setenv("APOLLO_NOTIFIER_IN_BOT", "1")
    ids = _ids(bot)
    assert "reply_agent_tick" in ids
    assert "apollo_notifier_tick" in ids


def test_modulo_apagado_gana_a_los_flags(bot, monkeypatch):
    """Un tenant SIN prospección contratada no corre estos jobs aunque los
    flags queden seteados por error."""
    import core_config
    monkeypatch.setenv("REPLY_AGENT_IN_BOT", "1")
    monkeypatch.setenv("APOLLO_NOTIFIER_IN_BOT", "1")
    monkeypatch.setitem(core_config.MODULES, "prospecting", False)
    ids = _ids(bot)
    assert "reply_agent_tick" not in ids
    assert "apollo_notifier_tick" not in ids


def test_notifier_state_respeta_state_dir(tmp_path, monkeypatch):
    """F4.3: el state del notifier era Path.home() crudo — en el App Service
    caía fuera del storage persistente. Ahora respeta STATE_DIR."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import apollo_completion_notifier as acn
    acn = importlib.reload(acn)
    assert str(acn.STATE_PATH).startswith(str(tmp_path))
    acn.save_state({"seq1": {"notified_at": "2026-07-03"}})
    assert acn.load_state() == {"seq1": {"notified_at": "2026-07-03"}}


def test_notifier_email_por_graph_mail_en_app_service(monkeypatch):
    """Con los secrets del Service Principal presentes (App Service), el
    notifier envía por graph_mail — sin refresh token que expire."""
    monkeypatch.setenv("MICROSOFT_APP_ID", "app-id-x")
    import apollo_completion_notifier as acn
    acn = importlib.reload(acn)
    import graph_mail
    envios: list[dict] = []
    monkeypatch.setattr(graph_mail, "send", lambda **kw: envios.append(kw),
                        raising=False)
    acn._send_notify_email("asunto", "<p>hola</p>")
    assert len(envios) == 1
    assert envios[0]["to"] == acn.NOTIFY_TO


def test_notifier_email_fallback_msal_en_pc(monkeypatch):
    """Sin secrets del SP (PC de Mateo), cae al camino MSAL de siempre."""
    monkeypatch.delenv("MICROSOFT_APP_ID", raising=False)
    import apollo_completion_notifier as acn
    acn = importlib.reload(acn)
    llamadas: list[dict] = []
    monkeypatch.setattr(acn, "send_email", lambda **kw: llamadas.append(kw))
    acn._send_notify_email("asunto", "<p>hola</p>")
    assert len(llamadas) == 1
    assert llamadas[0]["interactive_ok"] is False
