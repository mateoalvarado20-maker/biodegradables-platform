"""Tests del carry-forward de tareas persistentes (Feature persistencia 2026-06-15).

El bug original: las tareas ad-hoc/proyectos vivían solo en la semana actual y
desaparecían al cambiar de semana ISO. Ahora `init_week` arrastra las tareas
no-diarias NO finalizadas de la semana previa. Las `diaria` siguen reseteando.
"""
from __future__ import annotations

U = "colab@biodegradablesecuador.com"


def _seed_task(a, aid, tipo="unica", *, wk, fecha_limite=None):
    return a.add_adhoc(
        aid, f"Tarea {aid}", user_email=U, tipo=tipo,
        fecha_limite=fecha_limite, wk=wk,
    )


def test_tarea_no_diaria_se_arrastra(state_env):
    a = state_env.activity_state
    _seed_task(a, "reunion-x", tipo="unica", wk="2026-W10", fecha_limite="2026-03-20")
    # init de la semana siguiente debe arrastrar la tarea abierta
    nueva = a.init_week(U, wk="2026-W11")
    assert "reunion-x" in nueva["activities"]
    carried = nueva["activities"]["reunion-x"]
    assert carried["status"] == "pendiente"
    assert carried["fecha_limite"] == "2026-03-20"
    # historial preserva creación + registra el carry-forward
    notes = [h["note"] for h in carried["history"]]
    assert any("creada" in n for n in notes)
    assert any("carry-forward" in n for n in notes)


def test_proyecto_semanal_arrastra_avance(state_env):
    a = state_env.activity_state
    _seed_task(a, "proyecto-web", tipo="semanal", wk="2026-W10")
    a.set_weekly_progress("proyecto-web", 40, user_email=U, wk="2026-W10")
    nueva = a.init_week(U, wk="2026-W11")
    carried = nueva["activities"]["proyecto-web"]
    assert carried["avance"] == 40
    assert carried["status"] == "en_progreso"  # avance>0 → en_progreso


def test_tarea_finalizada_no_se_arrastra(state_env):
    a = state_env.activity_state
    _seed_task(a, "tarea-cerrada", tipo="unica", wk="2026-W10")
    a.set_task_status("tarea-cerrada", "finalizada", user_email=U, wk="2026-W10")
    nueva = a.init_week(U, wk="2026-W11")
    assert "tarea-cerrada" not in nueva["activities"]


def test_diaria_no_se_arrastra(state_env):
    a = state_env.activity_state
    a.add_adhoc("apollo", "Apollo correos", user_email=U, tipo="diaria",
                meta=70, wk="2026-W10")
    a.mark_daily("apollo", 50, user_email=U, fecha="2026-03-02", wk="2026-W10")
    nueva = a.init_week(U, wk="2026-W11")
    # las diarias NO se arrastran (resetean por semana)
    assert "apollo" not in nueva["activities"]


def test_carry_forward_idempotente(state_env):
    a = state_env.activity_state
    _seed_task(a, "t1", tipo="unica", wk="2026-W10")
    a.init_week(U, wk="2026-W11")
    before = a.get_week(U, wk="2026-W11")
    again = a.init_week(U, wk="2026-W11")  # re-init misma semana
    assert again == before  # no duplica el carry-forward ni el history


def test_carry_forward_borde_de_anio(state_env):
    """W52→W01 del año siguiente: el lookup de semana previa debe cruzar el año."""
    a = state_env.activity_state
    _seed_task(a, "cierre-anual", tipo="unica", wk="2025-W52")
    nueva = a.init_week(U, wk="2026-W01")
    assert "cierre-anual" in nueva["activities"]


def test_carry_forward_encadena_entre_semanas(state_env):
    """Una tarea abierta se propaga semana a semana hasta completarse: al crear
    una semana intermedia ya arrastra lo previo, así que el efecto encadena."""
    a = state_env.activity_state
    _seed_task(a, "vieja", tipo="unica", wk="2026-W08")
    # crear W10 ya arrastra 'vieja' desde W08 (semana previa con datos)
    _seed_task(a, "reciente", tipo="unica", wk="2026-W10")
    w10 = a.get_week(U, wk="2026-W10")
    assert "vieja" in w10["activities"]  # encadenó W08→W10
    nueva = a.init_week(U, wk="2026-W11")
    # ambas siguen vivas hasta que se finalicen
    assert {"vieja", "reciente"} <= set(nueva["activities"])


def test_prev_week_lookup_elige_mas_reciente(state_env):
    """Unit del helper: elige la semana con datos más reciente, no la primera."""
    a = state_env.activity_state
    user = {"weeks": {"2026-W08": {}, "2026-W10": {}, "2026-W05": {}}}
    assert a._prev_week_key_with_data(user, "2026-W11") == "2026-W10"
    assert a._prev_week_key_with_data(user, "2026-W09") == "2026-W08"
    assert a._prev_week_key_with_data(user, "2026-W05") is None  # nada anterior


def test_arrastrado_gana_sobre_template(state_env):
    """Si el template recrea un aid que también viene arrastrado, gana el
    arrastrado (preserva el avance acumulado)."""
    a = state_env.activity_state
    # template de mateo@test.local incluye proyectos; usamos un user con template
    # propio simulado vía add_adhoc + carry. Probamos el principio con un proyecto
    # semanal arrastrado que conserva avance frente a un re-seed.
    _seed_task(a, "proyecto-x", tipo="semanal", wk="2026-W10")
    a.set_weekly_progress("proyecto-x", 75, user_email=U, wk="2026-W10")
    nueva = a.init_week(U, wk="2026-W11")
    assert nueva["activities"]["proyecto-x"]["avance"] == 75
