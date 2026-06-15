"""Tests del resumen de carga por colaborador (Feature 2026-06-15)."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_KNOWN = (
    "mateo:malvarado@biodegradablesecuador.com,"
    "gabriela:gsanchez@biodegradablesecuador.com"
)
MATEO = "malvarado@biodegradablesecuador.com"


@pytest.fixture()
def ra(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("KNOWN_COLLABORATORS", _KNOWN)
    import safe_json
    importlib.reload(safe_json)
    import activity_state
    importlib.reload(activity_state)
    import reminders
    importlib.reload(reminders)
    import ask_agent
    return importlib.reload(ask_agent)


def test_rollup_cuenta_por_estado(ra):
    a = ra.activity_state
    # User sin template propio (no es el default) → arranca vacío, conteo limpio.
    colab = "colab@biodegradablesecuador.com"
    a.add_adhoc("t1", "Tarea 1", user_email=colab, tipo="unica")
    a.add_adhoc("t2", "Tarea 2", user_email=colab, tipo="unica", fecha_limite="2020-01-01")
    a.add_adhoc("t3", "Proyecto", user_email=colab, tipo="semanal")
    a.set_task_status("t1", "finalizada", user_email=colab)
    a.set_weekly_progress("t3", 30, user_email=colab)
    r = ra._workload_rollup(colab)
    assert r["finalizadas"] == 1
    assert r["vencidas"] == 1      # t2 fecha pasada → vencida derivada
    assert r["en_progreso"] == 1   # t3 avance>0 → en_progreso
    assert r["pendientes"] == 0
    assert any(p["nombre"] == "Tarea 2" and p["status"] == "vencida"
               for p in r["proximas"])


def test_team_excluye_supervisores_y_unidentified(ra):
    a = ra.activity_state
    a.add_adhoc("x", "X", user_email=MATEO, tipo="unica")
    a.add_adhoc("y", "Y", user_email="dsanchez@biodegradablesecuador.com", tipo="unica")
    a.add_adhoc("z", "Z", user_email="unidentified-abc@biodegradablesecuador.com",
                tipo="unica")
    emails = ra._team_collaborator_emails()
    assert MATEO in emails
    assert "dsanchez@biodegradablesecuador.com" not in emails  # supervisor
    assert not any(e.startswith("unidentified-") for e in emails)


def test_workload_text_individual(ra):
    a = ra.activity_state
    a.add_adhoc("t", "Reunion importante", user_email=MATEO, tipo="unica",
                fecha_limite="2099-01-01")
    txt = ra._workload_text_for_chat(MATEO)
    assert "Reunion importante" in txt
    assert "2099-01-01" in txt


def test_team_workload_html_render(ra):
    a = ra.activity_state
    a.add_adhoc("t", "Tarea vencida", user_email=MATEO, tipo="unica",
                fecha_limite="2020-01-01")
    html = ra._team_workload_html()
    assert "Carga de tareas del equipo" in html
    assert "Tarea vencida" in html
