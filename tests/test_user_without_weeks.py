"""Regresión 2026-06-16: un usuario en el state SIN la key 'weeks' (creado por
cierres_caja / rutas / pseudo-user viejo) hacía reventar con KeyError 'weeks'
los jobs que recorren TODOS los usuarios (task_confirmations, workload)."""
from __future__ import annotations

import pytest


def test_user_sin_weeks_no_crashea_list_all(state_env):
    a = state_env.activity_state
    # Usuario creado por otra vía: solo tiene cierres_caja, sin "weeks".
    state = a.load()
    state.setdefault("users", {})["raro@biodegradablesecuador.com"] = {
        "cierres_caja": {}
    }
    a.save(state)

    # Antes del fix: KeyError 'weeks'. Ahora debe devolver dict sin reventar.
    result = a.list_open_tasks_all_users()
    assert isinstance(result, dict)


def test_get_user_state_garantiza_weeks(state_env):
    a = state_env.activity_state
    state = a.load()
    state.setdefault("users", {})["josesito@biodegradablesecuador.com"] = {
        "rutas": {}
    }
    user = a._get_user_state(state, "josesito@biodegradablesecuador.com")
    assert "weeks" in user
    assert user["rutas"] == {}  # no pisa lo que ya tenía


def test_init_week_sobre_usuario_sin_weeks(state_env):
    a = state_env.activity_state
    state = a.load()
    state.setdefault("users", {})["x@biodegradablesecuador.com"] = {"cierres_caja": {}}
    a.save(state)
    # init_week no debe romper y debe crear la semana
    wk = a.init_week("x@biodegradablesecuador.com")
    assert "activities" in wk
