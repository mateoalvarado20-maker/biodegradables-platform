"""Tests del helper de asistencia/horario compartido (check-in + card de José,
2026-06-19). Verifica el parseo de la franja y los ids del card."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Si faltara alguna dep del bot (botbuilder/fastapi) en el entorno, se saltea.
teams_bot = pytest.importorskip("teams_bot")


def test_horario_card_items_tiene_los_ids():
    items = teams_bot._horario_card_items(date(2026, 6, 19))
    ids = {it.get("id") for it in items if it.get("id")}
    assert {"horario_estandar", "horario_notifico", "horario_horas_permiso",
            "horario_franja", "horario_motivo", "horario_porque_no_notifico"} <= ids


def test_save_horario_estandar(monkeypatch):
    cap = {}
    monkeypatch.setattr(teams_bot.activity_state, "set_day_schedule",
                        lambda user, fecha, **kw: cap.update({"user": user, **kw}))
    teams_bot._save_horario_from_form({"horario_estandar": "si"}, "u@x.com")
    assert cap["user"] == "u@x.com"
    assert cap["estandar"] is True


def test_save_horario_no_parsea_franja(monkeypatch):
    cap = {}
    monkeypatch.setattr(teams_bot.activity_state, "set_day_schedule",
                        lambda user, fecha, **kw: cap.update(kw))
    teams_bot._save_horario_from_form({
        "horario_estandar": "no",
        "horario_franja": "9:30 – 11:00",
        "horario_motivo": "reunión médica",
        "horario_notifico": "si_correo",
    }, "u@x.com")
    assert cap["estandar"] is False
    assert cap["desde"] == "9:30"
    assert cap["hasta"] == "11:00"
    assert "reunión médica" in cap["razon"]
    assert "correo" in cap["razon"].lower()


def test_save_horario_no_sin_franja(monkeypatch):
    cap = {}
    monkeypatch.setattr(teams_bot.activity_state, "set_day_schedule",
                        lambda user, fecha, **kw: cap.update(kw))
    teams_bot._save_horario_from_form({"horario_estandar": "no"}, "u@x.com")
    assert cap["estandar"] is False
    assert cap["desde"] == ""
    assert cap["hasta"] == ""
