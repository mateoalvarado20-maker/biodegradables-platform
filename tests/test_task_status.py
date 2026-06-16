"""Tests de status de tareas persistentes y derivación de 'vencida'."""
from __future__ import annotations

from datetime import date

import pytest

U = "colab@biodegradablesecuador.com"
WK = "2026-W10"


def _task(a, aid="t", tipo="unica", fecha_limite=None):
    return a.add_adhoc(aid, "Tarea", user_email=U, tipo=tipo,
                       fecha_limite=fecha_limite, wk=WK)


# ---------- status efectivo / vencida derivada ----------

def test_vencida_se_deriva_no_se_persiste(state_env):
    a = state_env.activity_state
    _task(a, fecha_limite="2020-01-01")  # fecha pasada
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    # el status GUARDADO sigue pendiente
    assert entry["status"] == "pendiente"
    # pero el EFECTIVO es vencida
    assert a.task_effective_status(entry) == "vencida"


def test_future_no_es_vencida(state_env):
    a = state_env.activity_state
    _task(a, fecha_limite="2099-12-31")
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert a.task_effective_status(entry) == "pendiente"


def test_finalizada_nunca_es_vencida(state_env):
    a = state_env.activity_state
    _task(a, fecha_limite="2020-01-01")
    a.set_task_status("t", "finalizada", user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert a.task_effective_status(entry) == "finalizada"


# ---------- transiciones ----------

def test_set_task_status_registra_historial(state_env):
    a = state_env.activity_state
    _task(a)
    a.set_task_status("t", "en_progreso", user_email=U, wk=WK, by="mateo@x", note="arrancada")
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert entry["status"] == "en_progreso"
    last = entry["history"][-1]
    assert last["from"] == "pendiente" and last["to"] == "en_progreso"
    assert last["by"] == "mateo@x"


def test_finalizada_pone_avance_100(state_env):
    a = state_env.activity_state
    _task(a, tipo="semanal")
    a.set_task_status("t", "finalizada", user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert entry["status"] == "finalizada"
    assert entry["avance"] == 100


def test_avance_100_no_finaliza_solo(state_env):
    """Llegar a 100% de avance NO finaliza la tarea — requiere confirmación."""
    a = state_env.activity_state
    _task(a, tipo="semanal")
    a.set_weekly_progress("t", 100, user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert entry["status"] == "en_progreso"  # NO finalizada


def test_status_invalido_lanza(state_env):
    a = state_env.activity_state
    _task(a)
    with pytest.raises(ValueError):
        a.set_task_status("t", "borrada", user_email=U, wk=WK)


def test_status_solo_aplica_a_tareas(state_env):
    a = state_env.activity_state
    a.add_adhoc("d", "Diaria", user_email=U, tipo="diaria", meta=10, wk=WK)
    with pytest.raises(ValueError):
        a.set_task_status("d", "finalizada", user_email=U, wk=WK)


# ---------- fecha límite / snooze ----------

def test_set_fecha_limite_valida_formato(state_env):
    a = state_env.activity_state
    _task(a)
    a.set_task_fecha_limite("t", "2026-04-15", user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert entry["fecha_limite"] == "2026-04-15"
    with pytest.raises(ValueError):
        a.set_task_fecha_limite("t", "no-es-fecha", user_email=U, wk=WK)


def test_snooze_saca_de_vencida(state_env):
    a = state_env.activity_state
    _task(a, fecha_limite="2020-01-01")
    a.snooze_task("t", 7, user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    # nueva fecha = hoy + 7 (porque estaba vencida, cuenta desde hoy)
    nueva = date.fromisoformat(entry["fecha_limite"])
    assert nueva >= date.today()  # ya no está en el pasado
    assert a.task_effective_status(entry) != "vencida"


def test_snooze_futuro_cuenta_desde_fecha(state_env):
    a = state_env.activity_state
    _task(a, fecha_limite="2099-01-01")
    a.snooze_task("t", 5, user_email=U, wk=WK)
    entry = a.get_week(U, wk=WK)["activities"]["t"]
    assert entry["fecha_limite"] == "2099-01-06"


# ---------- lecturas agregadas ----------

def test_list_open_tasks_excluye_finalizadas(state_env):
    a = state_env.activity_state
    _task(a, aid="abierta")
    _task(a, aid="cerrada")
    a.set_task_status("cerrada", "finalizada", user_email=U, wk=WK)
    abiertas = {aid for aid, _, _ in a.list_open_tasks(U, wk=WK)}
    assert abiertas == {"abierta"}


def test_list_open_tasks_all_users(state_env):
    a = state_env.activity_state
    _task(a, aid="t1")
    a.add_adhoc("t2", "Otra", user_email="otro@biodegradablesecuador.com",
                tipo="unica", wk=WK)
    todos = a.list_open_tasks_all_users(wk=WK)
    assert U in todos
    assert "otro@biodegradablesecuador.com" in todos
