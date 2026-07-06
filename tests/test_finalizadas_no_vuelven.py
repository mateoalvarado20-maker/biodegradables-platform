"""Las tareas puntuales/proyectos FINALIZADOS (o quitados) no vuelven el lunes
(2026-07-06). Antes el template los re-sembraba frescos cada semana aunque el
colaborador ya los cerró al 100%. Las diarias siguen re-sembrando siempre.
Registro persistente: user["tareas_cerradas"]."""
from __future__ import annotations

import copy

U = "colab@biodegradablesecuador.com"

TEMPLATE = {
    "activities": [
        {"id": "apollo", "nombre": "Apollo correos", "tipo": "diaria", "meta": 70},
        {"id": "proyecto-web", "nombre": "Proyecto web", "tipo": "semanal"},
        {"id": "manual-bodega", "nombre": "Manual de bodega", "tipo": "unica"},
    ]
}


def _patch_template(a, monkeypatch):
    monkeypatch.setattr(
        a, "load_template", lambda email=None: copy.deepcopy(TEMPLATE)
    )


def test_finalizada_de_template_no_vuelve_nunca(state_env, monkeypatch):
    a = state_env.activity_state
    _patch_template(a, monkeypatch)
    a.init_week(U, wk="2026-W10")
    a.set_task_status("proyecto-web", "finalizada", user_email=U, wk="2026-W10")

    w11 = a.init_week(U, wk="2026-W11")
    assert "proyecto-web" not in w11["activities"]   # finalizada → no vuelve
    assert "apollo" in w11["activities"]             # diaria → siempre vuelve
    assert "manual-bodega" in w11["activities"]      # no finalizada → sigue

    # Y tampoco DOS lunes después (el registro sobrevive semanas)
    w12 = a.init_week(U, wk="2026-W12")
    assert "proyecto-web" not in w12["activities"]
    assert "apollo" in w12["activities"]


def test_quitada_de_template_no_vuelve(state_env, monkeypatch):
    a = state_env.activity_state
    _patch_template(a, monkeypatch)
    a.init_week(U, wk="2026-W10")
    assert a.remove_activity("manual-bodega", user_email=U, wk="2026-W10")

    w11 = a.init_week(U, wk="2026-W11")
    assert "manual-bodega" not in w11["activities"]


def test_diaria_quitada_si_vuelve(state_env, monkeypatch):
    """Las diarias recurren SIEMPRE — quitar una del card de la semana no la
    saca del template (para retirarla de verdad se edita el template)."""
    a = state_env.activity_state
    _patch_template(a, monkeypatch)
    a.init_week(U, wk="2026-W10")
    assert a.remove_activity("apollo", user_email=U, wk="2026-W10")

    w11 = a.init_week(U, wk="2026-W11")
    assert "apollo" in w11["activities"]


def test_recolocar_la_revive(state_env, monkeypatch):
    a = state_env.activity_state
    _patch_template(a, monkeypatch)
    a.init_week(U, wk="2026-W10")
    a.set_task_status("proyecto-web", "finalizada", user_email=U, wk="2026-W10")
    a.reset_task_para_rehacer(
        "proyecto-web", user_email=U, wk="2026-W10", fecha_limite="2026-03-20"
    )
    w11 = a.init_week(U, wk="2026-W11")
    assert "proyecto-web" in w11["activities"]
    assert w11["activities"]["proyecto-web"]["status"] == "pendiente"


def test_readd_del_mismo_aid_la_revive(state_env, monkeypatch):
    """Si gerencia vuelve a delegar el mismo aid, la tarea reaparece normal."""
    a = state_env.activity_state
    _patch_template(a, monkeypatch)
    a.init_week(U, wk="2026-W10")
    a.set_task_status("manual-bodega", "finalizada", user_email=U, wk="2026-W10")

    w11 = a.init_week(U, wk="2026-W11")
    assert "manual-bodega" not in w11["activities"]

    a.add_adhoc("manual-bodega", "Manual de bodega v2", user_email=U,
                tipo="unica", wk="2026-W11")
    w12 = a.init_week(U, wk="2026-W12")
    assert "manual-bodega" in w12["activities"]  # re-delegada → viva otra vez


def test_adhoc_finalizada_sigue_sin_volver(state_env):
    """El comportamiento previo (ad-hoc finalizada no se arrastra) se mantiene."""
    a = state_env.activity_state
    a.add_adhoc("tarea-adhoc", "Tarea", user_email=U, tipo="unica", wk="2026-W10")
    a.set_task_status("tarea-adhoc", "finalizada", user_email=U, wk="2026-W10")
    w11 = a.init_week(U, wk="2026-W11")
    assert "tarea-adhoc" not in w11["activities"]
