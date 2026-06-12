"""Tests anti-contaminación entre usuarios y de concurrencia del tracker.

Es la suite que el dueño del proyecto pidió explícitamente: detectar de
inmediato cualquier cruce de datos entre actividades/usuarios, incluso bajo
ejecución concurrente (síntoma reportado: "las actividades se mezclan").
"""
from __future__ import annotations

import threading

import pytest

USERS = [f"user{i}@biodegradablesecuador.com" for i in range(5)]


def _seed_user(activity_state, email: str, aid: str):
    activity_state.add_adhoc(
        aid, f"Actividad de {email}",
        user_email=email, tipo="diaria", meta=10, unidad="u",
    )


# ---------- Aislamiento básico ----------

def test_cada_actividad_queda_en_su_usuario(state_env):
    a = state_env.activity_state
    for i, u in enumerate(USERS):
        _seed_user(a, u, f"act-{i}")
        a.mark_daily(f"act-{i}", i + 1, user_email=u)

    state = a.load()
    for i, u in enumerate(USERS):
        week = a.get_week(u)
        # Su actividad está, con SU valor
        assert f"act-{i}" in week["activities"]
        log = week["activities"][f"act-{i}"]["log"]
        assert any(rec["valor"] == i + 1 for rec in log.values())
        # Y NINGUNA actividad de otro usuario se filtró a su semana
        ajenas = {f"act-{j}" for j in range(len(USERS)) if j != i}
        assert not (ajenas & set(week["activities"])), (
            f"CONTAMINACIÓN: {u} contiene actividades de otro usuario"
        )
    # Ningún usuario inesperado apareció en el state
    assert set(state["users"]) == {u.lower() for u in USERS}


def test_usuario_nuevo_no_hereda_template_de_mateo(state_env):
    """Un usuario sin template propio arranca VACÍO (no con las de Mateo)."""
    a = state_env.activity_state
    week = a.get_week("nuevo@biodegradablesecuador.com")
    assert week["activities"] == {}


def test_init_week_idempotente(state_env):
    a = state_env.activity_state
    u = USERS[0]
    _seed_user(a, u, "act-x")
    a.mark_daily("act-x", 5, user_email=u)
    before = a.get_week(u)
    again = a.init_week(u)  # re-init de la misma semana
    assert again == before  # no duplica ni resetea


def test_marcar_actividad_inexistente_lanza(state_env):
    a = state_env.activity_state
    with pytest.raises(ValueError):
        a.mark_daily("no-existe", 1, user_email=USERS[0])


# ---------- Concurrencia: el síntoma reportado ----------

def test_concurrencia_sin_perdida_ni_cruce(state_env):
    """N usuarios marcando a la vez (handlers + threads + scheduler simulados).

    Antes (RMW sin lock): el último save() pisaba a los demás — marcas
    desaparecidas. Ahora: todas las marcas presentes, cada una en su usuario.
    """
    a = state_env.activity_state
    DAYS = [f"2026-06-{d:02d}" for d in range(8, 13)]  # lun-vie
    for i, u in enumerate(USERS):
        _seed_user(a, u, f"act-{i}")

    errors: list[Exception] = []

    def worker(i: int, u: str):
        try:
            for d in DAYS:
                a.mark_daily(f"act-{i}", i * 100 + int(d[-2:]), user_email=u, fecha=d)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i, u)) for i, u in enumerate(USERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for i, u in enumerate(USERS):
        log = a.get_week(u)["activities"][f"act-{i}"]["log"]
        assert set(log) == set(DAYS), f"marcas perdidas para {u}: {sorted(log)}"
        for d in DAYS:
            assert log[d]["valor"] == i * 100 + int(d[-2:]), (
                f"CONTAMINACIÓN: valor ajeno en {u} día {d}"
            )


def test_concurrencia_add_adhoc_mismo_usuario(state_env):
    """Varios add_adhoc concurrentes al mismo usuario: ninguno se pierde."""
    a = state_env.activity_state
    u = USERS[0]
    N = 20

    def worker(i: int):
        a.add_adhoc(f"t-{i}", f"Tarea {i}", user_email=u, tipo="semanal")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    acts = a.get_week(u)["activities"]
    assert {f"t-{i}" for i in range(N)} <= set(acts)


# ---------- Recuperación: el state del tracker sobrevive corrupción ----------

def test_corrupcion_de_activity_state_no_borra_semanas(state_env):
    a = state_env.activity_state
    u = USERS[0]
    _seed_user(a, u, "act-0")
    a.mark_daily("act-0", 7, user_email=u)
    # asegura .bak poblado (segunda escritura)
    a.mark_daily("act-0", 8, user_email=u)

    # Crash simulado: archivo truncado
    a.STATE_PATH.write_text('{"users": {"user0@', encoding="utf-8")

    week = a.get_week(u)  # internamente load() → restaura del .bak
    assert "act-0" in week["activities"]
    log = week["activities"]["act-0"]["log"]
    assert any(rec["valor"] in (7, 8) for rec in log.values())


# ---------- Reminders: carrera entrega-vs-creación (auditoría H6/H13) ----------

def test_reminders_concurrentes_no_se_pisan(state_env):
    r = state_env.reminders
    base = r.add_reminder("a@x.com", "2020-01-01T08:00:00-05:00", "vencido ya")

    created_ids: list[str] = []
    lock = threading.Lock()

    def creator(i: int):
        rec = r.add_reminder(f"u{i}@x.com", "2030-01-01T08:00:00-05:00", f"futuro {i}")
        with lock:
            created_ids.append(rec["id"])

    def deliverer():
        r.mark_sent(base["id"])

    ts = [threading.Thread(target=creator, args=(i,)) for i in range(10)]
    ts.append(threading.Thread(target=deliverer))
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    state = r.load()
    by_id = {x["id"]: x for x in state["reminders"]}
    # El flag sent NO se perdió por un save concurrente con copia vieja
    assert by_id[base["id"]]["sent"] is True
    # Y ningún reminder recién creado desapareció
    assert set(created_ids) <= set(by_id)
    # El vencido ya no aparece como due (no se re-entregaría cada 5 min)
    assert base["id"] not in {x["id"] for x in r.get_due_reminders()}


def test_dispatch_marks_concurrentes(state_env):
    d = state_env.dispatch_state
    N = 30

    def worker(i: int):
        d.mark(f"001-001-{i:09d}", "OK", marcado_por=f"hilo-{i}")

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(N)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    state = d.load()
    assert len(state) == N  # ninguna marca perdida
    assert all(d.is_ok(f"001-001-{i:09d}") for i in range(N))
