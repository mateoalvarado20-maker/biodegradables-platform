"""Persistencia del estado de actividades semanales (per-user).

State file: `~/.claude-agent/activity_state.json`. Estructura per-user:

    {
        "users": {
            "malvarado@biodegradablesecuador.com": {
                "weeks": {
                    "2026-W22": {
                        "started_at": "2026-05-25T...",
                        "lunes": "2026-05-25",
                        "viernes": "2026-05-29",
                        "activities": {
                            "apollo-correos": {
                                "nombre": "Apollo: correos enviados",
                                "tipo": "diaria",
                                "meta": 70,
                                ...
                                "log": {"2026-05-25": {"valor": 72, ...}}
                            }
                        }
                    }
                }
            },
            "otro@biodegradablesecuador.com": { ... }
        }
    }

Phase D (2026-05-30):
- Refactor: cada colaborador tiene su propio set de weeks.
- Todas las funciones aceptan `user_email`. Si no se da, usa
  `TRACKER_TARGET_USER` env var (compat con CLI legacy).
- Cada usuario puede tener su propio template (`activities_template_<slug>.json`)
  o cae al template default `activities_template.json`.
"""
from __future__ import annotations

import copy
import json
import os
import functools
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

LOCAL_TZ = timezone(timedelta(hours=-5))

STATE_PATH = Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent")) / "activity_state.json"
TEMPLATE_DEFAULT = Path(__file__).parent / "activities_template.json"

VALID_TIPOS: tuple[str, ...] = ("diaria", "semanal", "unica")
VALID_FUENTES: tuple[str, ...] = ("auto", "manual")

# ===== Tareas persistentes (2026-06-15) =====
# Las actividades NO-diarias (unica/semanal) se vuelven tareas persistentes:
# sobreviven el cambio de semana ISO (carry-forward en init_week) hasta que se
# marcan `finalizada` con confirmación explícita. Las `diaria` (métricas
# recurrentes tipo Apollo 70 correos / cierre de caja) NO se arrastran: siguen
# reseteando por semana, que es lo correcto.
#   status guardado:  pendiente | en_progreso | finalizada
#   status DERIVADO:  vencida (fecha_limite < hoy y status != finalizada) — NO
#                     se persiste, para que Posponer/Actualizar fecha salgan del
#                     estado vencido solo moviendo la fecha.
VALID_TASK_STATUSES: tuple[str, ...] = ("pendiente", "en_progreso", "finalizada")
TASK_OPEN_STATUSES: tuple[str, ...] = ("pendiente", "en_progreso", "vencida")

DEFAULT_USER = os.environ.get(
    "TRACKER_TARGET_USER", "malvarado@biodegradablesecuador.com"
).strip().lower()


# ============ Utilidades ============
def _ensure_dir() -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _normalize_email(email: str | None) -> str:
    if not email:
        return DEFAULT_USER
    return email.strip().lower()


def _user_slug(email: str) -> str:
    """Convierte email a slug para template per-user: malvarado@... → malvarado."""
    return re.split(r"[@.]", email)[0]


# Lock re-entrante por archivo: las funciones mutadoras (decoradas con
# @_locked) mantienen el lock durante TODO el ciclo load→mutar→save, para que
# escritores concurrentes (handlers async, worker threads de ask_agent, jobs
# de APScheduler) no se pisen entre sí (auditoría H1/A2).
_LOCK = safe_json.lock_for(STATE_PATH)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapper


def load() -> dict[str, Any]:
    # safe_json: atómico + backup + cuarentena. Un archivo corrupto ya NO se
    # convierte en estado vacío silencioso (auditoría H2/A3).
    data = safe_json.load_json(STATE_PATH, lambda: {"users": {}})
    # Auto-migrate antiguos {weeks: ...} → {users: {DEFAULT_USER: {weeks: ...}}}
    if "weeks" in data and "users" not in data:
        data = {"users": {DEFAULT_USER: {"weeks": data["weeks"]}}}
    if "users" not in data:
        data["users"] = {}
    return data


def save(state: dict[str, Any]) -> None:
    safe_json.save_json(STATE_PATH, state, sort_keys=True)


def load_template(user_email: str | None = None) -> dict[str, Any]:
    """Carga el template del user específico si existe.

    El template default (activities_template.json) son las actividades de
    DEFAULT_USER (Mateo) — SOLO aplica para él. Cualquier otro usuario sin
    template propio arranca con la lista vacía, para que un user nuevo o un
    pseudo-user `unidentified-*` nunca herede las actividades de Mateo.
    """
    email = _normalize_email(user_email)
    slug = _user_slug(email)
    per_user_path = Path(__file__).parent / f"activities_template_{slug}.json"
    if per_user_path.exists():
        return json.loads(per_user_path.read_text(encoding="utf-8"))
    if email == DEFAULT_USER:
        return json.loads(TEMPLATE_DEFAULT.read_text(encoding="utf-8"))
    return {"activities": []}


def _today() -> date:
    return datetime.now(LOCAL_TZ).date()


def _now_iso() -> str:
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def week_key(d: date | None = None) -> str:
    d = d or _today()
    iso = d.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def week_range(wk: str) -> tuple[date, date]:
    year_s, w_s = wk.split("-W")
    year, w = int(year_s), int(w_s)
    monday = datetime.fromisocalendar(year, w, 1).date()
    return monday, monday + timedelta(days=4)


# ===== Horario estándar por día (2026-06-15) =====
# Lun-Vie: 8:30 AM – 5:30 PM. Sábado: jornada reducida 9:00 AM – 1:00 PM.
# Centralizado acá para que el Activity Bot (check-in card) y los resúmenes
# derivados muestren EXACTAMENTE la misma jornada según el día.
HORARIO_ESTANDAR_SEMANA = ("8:30 AM", "5:30 PM")
HORARIO_ESTANDAR_SABADO = ("9:00 AM", "1:00 PM")
HORARIO_ESTANDAR_SEMANA_CORTO = "8:30–17:30"
HORARIO_ESTANDAR_SABADO_CORTO = "9:00–13:00"


def _coerce_date(fecha: "str | date | None") -> date:
    if fecha is None:
        return _today()
    if isinstance(fecha, date):
        return fecha
    return date.fromisoformat(fecha)


def es_sabado(fecha: "str | date | None" = None) -> bool:
    """True si la fecha (default hoy EC) cae en sábado."""
    return _coerce_date(fecha).weekday() == 5


def horario_estandar(fecha: "str | date | None" = None) -> tuple[str, str]:
    """(desde, hasta) del horario estándar según el día.

    Sábado → 9:00 AM – 1:00 PM (jornada reducida GYE/sucursales).
    Resto  → 8:30 AM – 5:30 PM.
    """
    if es_sabado(fecha):
        return HORARIO_ESTANDAR_SABADO
    return HORARIO_ESTANDAR_SEMANA


def horario_estandar_label(fecha: "str | date | None" = None) -> str:
    """Etiqueta larga: "9:00 AM – 1:00 PM" (sáb) o "8:30 AM – 5:30 PM"."""
    desde, hasta = horario_estandar(fecha)
    return f"{desde} – {hasta}"


def horario_estandar_corto(fecha: "str | date | None" = None) -> str:
    """Etiqueta compacta: "9:00–13:00" (sáb) o "8:30–17:30"."""
    if es_sabado(fecha):
        return HORARIO_ESTANDAR_SABADO_CORTO
    return HORARIO_ESTANDAR_SEMANA_CORTO


def _get_user_state(state: dict, user_email: str) -> dict[str, Any]:
    email = _normalize_email(user_email)
    if email not in state["users"]:
        state["users"][email] = {"weeks": {}}
    user = state["users"][email]
    # Robustez (2026-06-16): un usuario puede existir SIN la key "weeks" — lo
    # crearon otras vías (cierres_caja, rutas, caja_chica) o pseudo-users viejos.
    # Garantizarla evita KeyError 'weeks' cuando un job recorre TODOS los users
    # (list_open_tasks_all_users / workload / task_confirmations).
    if "weeks" not in user:
        user["weeks"] = {}
    return user


# ============ Tareas persistentes: helpers (no mutan el state file) ============
def _is_task(entry: dict[str, Any] | None) -> bool:
    """True si el entry es una tarea no-diaria (unica/semanal) — persistente."""
    return bool(entry) and entry.get("tipo") in ("semanal", "unica")


def _parse_week_key(wk: str) -> tuple[int, int]:
    """`2026-W22` → (2026, 22). Lanza ValueError si el formato no calza."""
    year_s, w_s = wk.split("-W")
    return int(year_s), int(w_s)


def _prev_week_key_with_data(user: dict[str, Any], wk: str) -> str | None:
    """Clave de la semana con datos más reciente estrictamente anterior a `wk`.

    Parsea a (año, semana) para manejar el borde W52→W01 — NO confiar en el
    orden lexicográfico de los strings (`2025-W52` < `2026-W01` calza por
    casualidad, pero conviene ser explícito y robusto).
    """
    try:
        target = _parse_week_key(wk)
    except (ValueError, IndexError):
        return None
    best_key: str | None = None
    best_tuple: tuple[int, int] | None = None
    for k in user.get("weeks", {}):
        try:
            t = _parse_week_key(k)
        except (ValueError, IndexError):
            continue
        if t < target and (best_tuple is None or t > best_tuple):
            best_tuple, best_key = t, k
    return best_key


def _cerradas_set(user: dict[str, Any]) -> set[str]:
    """Aids de tareas NO-diarias finalizadas o quitadas por el colaborador
    (2026-07-06). Vive a nivel de usuario (sobrevive el cambio de semana):
    init_week y el seed del template NO re-siembran estos aids — solo las
    diarias recurren siempre. Se limpia al re-agregar el aid (add_adhoc) o al
    recolocar la tarea (reset_task_para_rehacer / status != finalizada)."""
    return set(user.get("tareas_cerradas") or [])


def _marcar_cerrada(user: dict[str, Any], aid: str, cerrada: bool) -> None:
    s = _cerradas_set(user)
    if cerrada:
        s.add(aid)
    else:
        s.discard(aid)
    user["tareas_cerradas"] = sorted(s)


def tareas_cerradas(user_email: str | None = None) -> set[str]:
    """Lectura pública (sin crear state) del registro de tareas cerradas."""
    email = _normalize_email(user_email)
    state = load()
    user = (state.get("users") or {}).get(email) or {}
    return _cerradas_set(user)


def _ensure_task_fields(entry: dict[str, Any], wk: str) -> dict[str, Any]:
    """Rellena (in-place) los campos de tarea persistente faltantes.

    Idempotente y de migración lazy: entries creados antes de esta feature no
    tienen status/history/fecha_limite — al tocarlos (carry-forward, add_adhoc,
    set_weekly_progress, mutadores de tarea) quedan bien formados sin reescribir
    semanas históricas enteras. Solo aplica a tareas no-diarias.
    """
    if not _is_task(entry):
        return entry
    if "status" not in entry:
        avance = entry.get("avance") or 0
        entry["status"] = "finalizada" if avance >= 100 else "pendiente"
    entry.setdefault("fecha_limite", None)
    created = (
        entry.get("created_at")
        or entry.get("ultima_actualizacion")
        or _now_iso()
    )
    entry.setdefault("created_at", created)
    entry.setdefault("updated_at", entry.get("ultima_actualizacion") or created)
    entry.setdefault("history", [])
    entry.setdefault("origen_wk", wk)
    entry.setdefault("calendar_event_id", None)
    entry.setdefault("calendar_web_link", None)
    entry.setdefault("calendar_synced_fecha", None)
    entry.setdefault("last_confirmation_asked", None)
    return entry


def task_effective_status(
    entry: dict[str, Any] | None, today: date | None = None
) -> str:
    """Status efectivo de una tarea no-diaria. '' si el entry no es tarea.

    'vencida' se DERIVA (fecha_limite < hoy y no finalizada); nunca se persiste,
    así Posponer/Actualizar fecha la sacan del estado vencido solo moviendo la
    fecha límite.
    """
    if not _is_task(entry):
        return ""
    status = entry.get("status") or "pendiente"
    if status == "finalizada":
        return "finalizada"
    fl = entry.get("fecha_limite")
    if fl:
        today = today or _today()
        try:
            if date.fromisoformat(fl) < today:
                return "vencida"
        except (ValueError, TypeError):
            pass
    return status


def _append_history(
    entry: dict[str, Any], frm: Any, to: Any, *, by: str, note: str
) -> None:
    entry.setdefault("history", []).append({
        "at": _now_iso(),
        "from": frm,
        "to": to,
        "by": by or "system",
        "note": note,
    })


# ============ Funciones principales (per-user) ============
@_locked
def init_week(
    user_email: str | None = None, wk: str | None = None
) -> dict[str, Any]:
    """Inicializa una semana desde la plantilla del usuario. Idempotente."""
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk in user["weeks"]:
        return user["weeks"][wk]

    template = load_template(email)
    monday, friday = week_range(wk)

    # 2026-07-06: los puntuales/proyectos FINALIZADOS (o quitados) no vuelven
    # el lunes — antes el template los re-sembraba frescos cada semana y el
    # colaborador los veía reaparecer aunque ya los cerró al 100%. Solo las
    # diarias re-siembran siempre (recurrentes por diseño).
    cerradas = _cerradas_set(user)

    activities: dict[str, dict[str, Any]] = {}
    for a in template["activities"]:
        if a["tipo"] != "diaria" and a["id"] in cerradas:
            continue
        entry: dict[str, Any] = {
            "nombre": a["nombre"],
            "tipo": a["tipo"],
            "meta": a.get("meta"),
            # meta_semanal (2026-06-19): meta acumulada de la semana (ej.
            # video-tiktok 5/semana). Antes no se copiaba del template, así que
            # el resumen no podía mostrar el avance semanal. Solo se setea si el
            # template la define, para no ensuciar las demás activities.
            **({"meta_semanal": a["meta_semanal"]} if a.get("meta_semanal") else {}),
            "unidad": a.get("unidad", ""),
            "fuente": a.get("fuente", "manual"),
            "adhoc": False,
        }
        if a["tipo"] == "diaria":
            entry["log"] = {}
        else:
            entry["avance"] = 0
            entry["notas"] = ""
            entry["ultima_actualizacion"] = None
            entry["fecha_limite"] = a.get("fecha_limite")
            _ensure_task_fields(entry, wk)
        activities[a["id"]] = entry

    # Carry-forward (2026-06-15): arrastra las tareas no-diarias NO finalizadas
    # de la semana con datos más reciente. Así las tareas ad-hoc/asignadas y los
    # proyectos sobreviven el cambio de semana en vez de desaparecer. La versión
    # arrastrada GANA sobre la del template (preserva estado/avance/historial);
    # las diarias nunca se arrastran (no colisionan).
    prev_wk = _prev_week_key_with_data(user, wk)
    if prev_wk:
        prev_acts = user["weeks"][prev_wk].get("activities", {}) or {}
        for aid, prev_entry in prev_acts.items():
            if not _is_task(prev_entry):
                continue
            _ensure_task_fields(prev_entry, prev_wk)
            if task_effective_status(prev_entry) == "finalizada":
                continue
            if aid in cerradas:
                continue  # cerrada por el colaborador — no revive
            carried = copy.deepcopy(prev_entry)
            carried["updated_at"] = _now_iso()
            _append_history(
                carried, carried.get("status"), carried.get("status"),
                by="system", note=f"carry-forward {prev_wk}→{wk}",
            )
            activities[aid] = carried

    user["weeks"][wk] = {
        "started_at": _now_iso(),
        "lunes": monday.isoformat(),
        "viernes": friday.isoformat(),
        "activities": activities,
    }
    save(state)
    return user["weeks"][wk]


def get_week(
    user_email: str | None = None, wk: str | None = None
) -> dict[str, Any]:
    return init_week(user_email, wk)


@_locked
def mark_daily(
    activity_id: str,
    valor: float | int,
    *,
    user_email: str | None = None,
    fecha: str | None = None,
    evidencia: str = "",
    notas: str = "",
    wk: str | None = None,
) -> dict[str, Any]:
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk not in user["weeks"]:
        init_week(email, wk)
        state = load()
        user = _get_user_state(state, email)

    activities = user["weeks"][wk]["activities"]
    if activity_id not in activities:
        raise ValueError(
            f"Actividad '{activity_id}' no existe en la semana {wk} de {email}."
        )

    entry = activities[activity_id]
    if entry["tipo"] != "diaria":
        raise ValueError(
            f"'{activity_id}' es tipo '{entry['tipo']}' — usá set_weekly_progress."
        )

    fecha = fecha or _today().isoformat()
    entry.setdefault("log", {})[fecha] = {
        "valor": valor,
        "marcado_at": _now_iso(),
        "evidencia": evidencia,
        "notas": notas,
    }
    save(state)
    return entry["log"][fecha]


@_locked
def set_weekly_progress(
    activity_id: str,
    avance: float,
    *,
    user_email: str | None = None,
    notas: str = "",
    wk: str | None = None,
) -> dict[str, Any]:
    if not (0 <= avance <= 100):
        raise ValueError("avance debe estar entre 0 y 100")

    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk not in user["weeks"]:
        init_week(email, wk)
        state = load()
        user = _get_user_state(state, email)

    activities = user["weeks"][wk]["activities"]
    if activity_id not in activities:
        raise ValueError(f"Actividad '{activity_id}' no existe ({email}).")

    entry = activities[activity_id]
    if entry["tipo"] == "diaria":
        raise ValueError(f"'{activity_id}' es diaria — usá mark_daily.")

    _ensure_task_fields(entry, wk)
    entry["avance"] = avance
    if notas:
        entry["notas"] = notas
    entry["ultima_actualizacion"] = _now_iso()
    entry["updated_at"] = entry["ultima_actualizacion"]
    # Avanzar no FINALIZA — eso requiere confirmación explícita (Feature 2).
    # Solo movemos pendiente→en_progreso cuando hay algún avance.
    if entry.get("status") == "pendiente" and avance > 0:
        prev = entry["status"]
        entry["status"] = "en_progreso"
        _append_history(entry, prev, "en_progreso", by="system", note="avance registrado")
    save(state)
    return entry


@_locked
def add_adhoc(
    activity_id: str,
    nombre: str,
    *,
    user_email: str | None = None,
    tipo: str = "unica",
    meta: float | int | None = None,
    unidad: str = "",
    fuente: str = "manual",
    fecha_limite: str | None = None,
    wk: str | None = None,
) -> dict[str, Any]:
    if tipo not in VALID_TIPOS:
        raise ValueError(f"tipo inválido: {tipo}")
    if fuente not in VALID_FUENTES:
        raise ValueError(f"fuente inválida: {fuente}")
    if fecha_limite:
        date.fromisoformat(fecha_limite)  # valida formato ISO (lanza si no)

    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk not in user["weeks"]:
        init_week(email, wk)
        state = load()
        user = _get_user_state(state, email)

    activities = user["weeks"][wk]["activities"]
    if activity_id in activities:
        raise ValueError(f"Ya existe '{activity_id}' en la semana {wk} ({email}).")

    entry: dict[str, Any] = {
        "nombre": nombre,
        "tipo": tipo,
        "meta": meta,
        "unidad": unidad,
        "fuente": fuente,
        "adhoc": True,
    }
    if tipo == "diaria":
        entry["log"] = {}
    else:
        entry["avance"] = 0
        entry["notas"] = ""
        entry["ultima_actualizacion"] = None
        entry["fecha_limite"] = fecha_limite
        _ensure_task_fields(entry, wk)
        _append_history(entry, None, "pendiente", by="system", note="creada")

    activities[activity_id] = entry
    # Re-agregar un aid lo saca del registro de cerradas (2026-07-06): si
    # gerencia vuelve a delegar la misma tarea, debe reaparecer normal.
    _marcar_cerrada(user, activity_id, False)
    save(state)
    return entry


@_locked
def set_activity_nombre(
    activity_id: str, nombre: str, *,
    user_email: str | None = None, wk: str | None = None,
) -> bool:
    """Actualiza el nombre visible de una activity existente (2026-07-06).
    Usado por el sync de cobranzas: el monto/atraso del cliente cambia con
    abonos y el card debe mostrar el valor actual. No toca log/avance/history.
    Devuelve True solo si cambió algo."""
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    acts = (user["weeks"].get(wk) or {}).get("activities") or {}
    entry = acts.get(activity_id)
    if entry is None or entry.get("nombre") == nombre:
        return False
    entry["nombre"] = nombre
    save(state)
    return True


@_locked
def remove_activity(
    activity_id: str, *, user_email: str | None = None, wk: str | None = None
) -> bool:
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk not in user["weeks"]:
        return False
    activities = user["weeks"][wk]["activities"]
    if activity_id not in activities:
        return False
    entry = activities[activity_id]
    del activities[activity_id]
    # 2026-07-06: quitar una tarea no-diaria también la registra como cerrada
    # — si está en el template, el lunes NO se re-siembra. Las diarias quedan
    # fuera del registro (recurren siempre; para retirarlas: editar template).
    if entry.get("tipo") != "diaria":
        _marcar_cerrada(user, activity_id, True)
    save(state)
    return True


VALID_PRIORITIES: tuple[str, ...] = ("alta", "media", "baja")


@_locked
def set_priority(
    activity_id: str,
    priority: str,
    *,
    user_email: str | None = None,
    wk: str | None = None,
) -> dict[str, Any]:
    """Setea la prioridad de una actividad (alta/media/baja).

    Phase L (2026-06-02): gerencia (Daniel/Gabriela) marca actividades por
    importancia. Las prioridad ALTA aparecen primero en check-in card y email,
    y si no se hicieron el día anterior re-aparecen en rojo (carry-over).
    """
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"priority debe ser uno de {VALID_PRIORITIES}, no '{priority}'")

    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if wk not in user["weeks"]:
        init_week(email, wk)
        state = load()
        user = _get_user_state(state, email)
    activities = user["weeks"][wk]["activities"]
    if activity_id not in activities:
        raise ValueError(
            f"Actividad '{activity_id}' no existe en la semana {wk} de {email}"
        )
    activities[activity_id]["priority"] = priority
    save(state)
    return activities[activity_id]


def get_priority(activity: dict[str, Any] | None) -> str:
    if not activity:
        return "media"
    return activity.get("priority", "media")


def is_carryover_alta(
    activity: dict[str, Any],
    today_iso: str,
    yesterday_iso: str,
) -> bool:
    """True si una actividad ALTA no se hizo ayer y todavía no se hizo hoy.

    Se usa para resaltar en rojo en card y email del día siguiente.
    Solo aplica a actividades diarias con priority='alta'.
    """
    if activity.get("tipo") != "diaria":
        return False
    if activity.get("priority") != "alta":
        return False
    log = activity.get("log", {})
    # Ya hecho hoy → no es carry-over
    rec_today = log.get(today_iso)
    if rec_today and (rec_today.get("valor") or 0) > 0:
        return False
    # Ayer: o no marcado, o marcado con valor=0 → carry-over
    rec_yesterday = log.get(yesterday_iso)
    if rec_yesterday is None:
        return True
    return (rec_yesterday.get("valor") or 0) == 0


def sort_activities_by_priority_then_carryover(
    activities: list[tuple[str, dict[str, Any]]],
    today_iso: str,
    yesterday_iso: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Ordena: carry-overs alta primero, después por priority alta→media→baja.

    Útil para construir cards/emails que pongan lo urgente primero.
    """
    PRIORITY_ORDER = {"alta": 0, "media": 1, "baja": 2}

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple:
        aid, a = item
        co = is_carryover_alta(a, today_iso, yesterday_iso)
        prio_rank = PRIORITY_ORDER.get(get_priority(a), 1)
        return (0 if co else 1, prio_rank)

    return sorted(activities, key=_sort_key)


# ============ Tareas persistentes: mutadores y lecturas (2026-06-15) ============
def _task_entry_for_mutation(
    state: dict, email: str, wk: str, activity_id: str
) -> tuple[dict, dict, dict]:
    """Devuelve (user, activities, entry) asegurando la semana y validando que
    el activity_id existe y es una tarea no-diaria. Lanza ValueError si no.
    """
    user = _get_user_state(state, email)
    if wk not in user["weeks"]:
        init_week(email, wk)  # re-entrante (RLock); materializa carry-forward
        state.clear()
        state.update(load())
        user = _get_user_state(state, email)
    activities = user["weeks"][wk]["activities"]
    if activity_id not in activities:
        raise ValueError(
            f"Tarea '{activity_id}' no existe en la semana {wk} de {email}."
        )
    entry = activities[activity_id]
    if not _is_task(entry):
        raise ValueError(
            f"'{activity_id}' es tipo '{entry.get('tipo')}' — status/fecha solo "
            f"aplican a tareas (unica/semanal)."
        )
    _ensure_task_fields(entry, wk)
    return user, activities, entry


@_locked
def set_task_status(
    activity_id: str,
    status: str,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    by: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Cambia el status de una tarea (pendiente/en_progreso/finalizada).

    'finalizada' es el ÚNICO estado terminal y solo debe setearse tras
    confirmación explícita (Feature 2). Registra la transición en `history`.
    """
    if status not in VALID_TASK_STATUSES:
        raise ValueError(f"status inválido: {status} (válidos: {VALID_TASK_STATUSES})")
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    user, _, entry = _task_entry_for_mutation(state, email, wk, activity_id)
    prev = entry.get("status")
    entry["status"] = status
    entry["updated_at"] = _now_iso()
    if status == "finalizada":
        entry["avance"] = 100
    # Registro de cerradas (2026-07-06): finalizada → el template no la
    # re-siembra el lunes; reabrirla (cualquier otro status) la desmarca.
    _marcar_cerrada(user, activity_id, status == "finalizada")
    _append_history(entry, prev, status, by=by, note=note)
    save(state)
    return entry


@_locked
def reset_task_para_rehacer(
    activity_id: str,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    fecha_limite: str | None = None,
    by: str = "",
) -> dict[str, Any]:
    """Recoloca una tarea no-diaria para rehacerla otro día (2026-06-24):
    avance=0, status='pendiente', y opcionalmente nueva fecha límite. Para la
    opción 'recolocar para otro día' cuando la tarea ya estaba al 100%."""
    if fecha_limite:
        date.fromisoformat(fecha_limite)  # valida formato (lanza ValueError)
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    user, _, entry = _task_entry_for_mutation(state, email, wk, activity_id)
    if entry.get("tipo") == "diaria":
        raise ValueError(f"'{activity_id}' es diaria — no se recoloca.")
    prev = entry.get("status")
    entry["avance"] = 0
    entry["status"] = "pendiente"
    _marcar_cerrada(user, activity_id, False)  # recolocada → puede volver
    if fecha_limite is not None:
        entry["fecha_limite"] = fecha_limite or None
    entry["ultima_actualizacion"] = _now_iso()
    entry["updated_at"] = entry["ultima_actualizacion"]
    nota = "recolocada para rehacer" + (f" (para {fecha_limite})" if fecha_limite else "")
    _append_history(entry, prev, "pendiente", by=by or "user", note=nota)
    save(state)
    return entry


@_locked
def set_task_fecha_limite(
    activity_id: str,
    fecha_limite: str | None,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    by: str = "",
) -> dict[str, Any]:
    """Fija/limpia la fecha límite (ISO YYYY-MM-DD o None). Valida el formato."""
    if fecha_limite:
        date.fromisoformat(fecha_limite)  # lanza ValueError si malformado
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    _, _, entry = _task_entry_for_mutation(state, email, wk, activity_id)
    prev = entry.get("fecha_limite")
    entry["fecha_limite"] = fecha_limite or None
    entry["updated_at"] = _now_iso()
    _append_history(entry, prev, fecha_limite, by=by, note="fecha_limite actualizada")
    save(state)
    return entry


@_locked
def snooze_task(
    activity_id: str,
    days: int,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    by: str = "",
) -> dict[str, Any]:
    """Posterga la fecha límite N días desde max(hoy, fecha_limite actual).

    Si está vencida (fecha en el pasado) cuenta desde hoy; si es futura, desde
    la fecha existente. Sale del estado 'vencida' derivado al mover la fecha.
    """
    days = int(days)
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    _, _, entry = _task_entry_for_mutation(state, email, wk, activity_id)
    base = _today()
    fl = entry.get("fecha_limite")
    if fl:
        try:
            base = max(base, date.fromisoformat(fl))
        except (ValueError, TypeError):
            base = _today()
    nueva = (base + timedelta(days=days)).isoformat()
    prev = entry.get("fecha_limite")
    entry["fecha_limite"] = nueva
    entry["updated_at"] = _now_iso()
    _append_history(entry, prev, nueva, by=by, note=f"pospuesta {days}d")
    save(state)
    return entry


@_locked
def set_task_calendar_ref(
    activity_id: str,
    event_id: str | None,
    web_link: str | None,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    synced_fecha: str | None = None,
) -> dict[str, Any] | None:
    """Guarda (o limpia) la referencia al evento de calendario de una tarea.

    Lo usa el job de sync de calendario (Feature 4) para evitar duplicados:
    si la tarea ya tiene event_id, se patchea/borra en vez de re-crear.
    `synced_fecha` = la fecha_limite con la que se creó/actualizó el evento, para
    detectar cuándo hay que mover el evento (la fecha cambió).
    """
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    user = state.get("users", {}).get(email, {})
    entry = (user.get("weeks", {}).get(wk, {}).get("activities", {}) or {}).get(activity_id)
    if entry is None:
        return None
    entry["calendar_event_id"] = event_id
    entry["calendar_web_link"] = web_link
    entry["calendar_synced_fecha"] = synced_fecha
    entry["updated_at"] = _now_iso()
    save(state)
    return entry


@_locked
def mark_task_confirmation_asked(
    activity_id: str,
    *,
    user_email: str | None = None,
    wk: str | None = None,
    fecha: str | None = None,
) -> dict[str, Any] | None:
    """Marca que hoy ya se pidió confirmación de cierre de esta tarea (anti-spam:
    el job de confirmaciones pregunta máximo una vez por día)."""
    email = _normalize_email(user_email)
    wk = wk or week_key()
    state = load()
    user = state.get("users", {}).get(email, {})
    entry = (user.get("weeks", {}).get(wk, {}).get("activities", {}) or {}).get(activity_id)
    if entry is None:
        return None
    entry["last_confirmation_asked"] = fecha or _today().isoformat()
    save(state)
    return entry


def list_tasks(
    user_email: str | None, wk: str | None = None
) -> list[tuple[str, dict[str, Any], str]]:
    """TODAS las tareas no-diarias de la semana (incluye finalizadas).

    Asegura la semana (init_week → materializa carry-forward). Cada item:
    (activity_id, entry, status_efectivo).
    """
    email = _normalize_email(user_email)
    wk = wk or week_key()
    week = init_week(email, wk)
    out: list[tuple[str, dict[str, Any], str]] = []
    for aid, entry in (week.get("activities") or {}).items():
        if not _is_task(entry):
            continue
        out.append((aid, entry, task_effective_status(entry)))
    return out


def list_open_tasks(
    user_email: str | None, wk: str | None = None
) -> list[tuple[str, dict[str, Any], str]]:
    """Tareas no-diarias abiertas (pendiente/en_progreso/vencida) de la semana."""
    return [t for t in list_tasks(user_email, wk) if t[2] in TASK_OPEN_STATUSES]


def list_open_tasks_all_users(
    wk: str | None = None,
) -> dict[str, list[tuple[str, dict[str, Any], str]]]:
    """{email: [(aid, entry, status_efectivo), ...]} de todos los users con
    tareas abiertas. Para el job de confirmaciones y el resumen de carga."""
    out: dict[str, list[tuple[str, dict[str, Any], str]]] = {}
    for email in list_known_users():
        tasks = list_open_tasks(email, wk)
        if tasks:
            out[email] = tasks
    return out


def get_user_months_summary(
    user_email: str | None,
    year: int,
    month: int,
) -> dict[str, Any]:
    """Agrega toda la actividad de un usuario en un mes específico.

    Phase M (2026-06-02): para el monthly activities recap. Retorna por
    actividad: total marcado, meta acumulada esperada, cumplimiento %,
    razones de fallos, priority.
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    weeks_in_month: list[tuple[str, dict[str, Any]]] = []
    for wk_key, wk_data in user.get("weeks", {}).items():
        monday_str = wk_data.get("lunes", "")
        try:
            wk_year = int(monday_str.split("-")[0])
            wk_month = int(monday_str.split("-")[1])
            if wk_year == year and wk_month == month:
                weeks_in_month.append((wk_key, wk_data))
        except (ValueError, IndexError):
            continue

    # Agregar por actividad ID
    agg_daily: dict[str, dict[str, Any]] = {}
    agg_weekly: dict[str, dict[str, Any]] = {}
    razones_no_hechas: list[dict[str, Any]] = []

    for wk_key, wk_data in weeks_in_month:
        for aid, a in wk_data.get("activities", {}).items():
            priority = a.get("priority", "media")
            nombre = a.get("nombre", aid)
            meta = a.get("meta")
            if a.get("tipo") == "diaria":
                if aid not in agg_daily:
                    agg_daily[aid] = {
                        "nombre": nombre,
                        "priority": priority,
                        "meta_diaria": meta,
                        "total_marcado": 0.0,
                        "dias_marcados": 0,
                        "dias_no_hechas": 0,
                    }
                for fecha, rec in (a.get("log") or {}).items():
                    valor = rec.get("valor") or 0
                    notas = rec.get("notas") or ""
                    agg_daily[aid]["total_marcado"] += valor
                    agg_daily[aid]["dias_marcados"] += 1
                    if valor == 0:
                        agg_daily[aid]["dias_no_hechas"] += 1
                        if notas:
                            razones_no_hechas.append({
                                "actividad": nombre,
                                "fecha": fecha,
                                "razon": notas,
                                "priority": priority,
                            })
            else:
                # weekly — quedarnos con el último avance del mes
                if aid not in agg_weekly:
                    agg_weekly[aid] = {
                        "nombre": nombre,
                        "priority": priority,
                        "avance_final": 0,
                        "notas": "",
                    }
                avance = a.get("avance") or 0
                if avance >= agg_weekly[aid]["avance_final"]:
                    agg_weekly[aid]["avance_final"] = avance
                    agg_weekly[aid]["notas"] = a.get("notas", "")

    return {
        "year": year,
        "month": month,
        "user": email,
        "weeks_in_month": [wk for wk, _ in weeks_in_month],
        "actividades_diarias": agg_daily,
        "actividades_semanales": agg_weekly,
        "razones_no_hechas": razones_no_hechas,
    }


@_locked
def set_day_schedule(
    user_email: str | None,
    fecha: str,
    estandar: bool,
    *,
    desde: str = "",
    hasta: str = "",
    razon: str = "",
) -> dict[str, Any]:
    """Guarda el horario trabajado en una fecha específica per-user.

    Phase K (2026-06-01): el check-in card del Activities Bot pregunta al
    inicio si el colaborador trabajó el horario estándar 8:30-17:30. Si no,
    pide desde/hasta/razon. Esto se incluye en el resumen diario a supervisores.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "day_schedules" not in user:
        user["day_schedules"] = {}
    user["day_schedules"][fecha] = {
        "estandar": bool(estandar),
        "desde": desde.strip(),
        "hasta": hasta.strip(),
        "razon": razon.strip(),
        "marcado_at": _now_iso(),
    }
    save(state)
    return user["day_schedules"][fecha]


def get_day_schedule(
    user_email: str | None, fecha: str
) -> dict[str, Any] | None:
    """Devuelve el horario guardado para una fecha, o None."""
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    return user.get("day_schedules", {}).get(fecha)


# ============ Cierre de caja (Phase N, 2026-06-02) ============
# Denominaciones USD: Ecuador está dolarizado y usa los mismos billetes/monedas que EEUU.
# El fondo de caja siempre es $50 — el "valor restante entregado" = total contado - 50.
CAJA_DENOMINACIONES_BILLETES: tuple[tuple[str, float], ...] = (
    ("b100", 100.00),
    ("b50", 50.00),
    ("b20", 20.00),
    ("b10", 10.00),
    ("b5", 5.00),
    ("b1", 1.00),
)
CAJA_DENOMINACIONES_MONEDAS: tuple[tuple[str, float], ...] = (
    ("m1", 1.00),
    ("m050", 0.50),
    ("m025", 0.25),
    ("m010", 0.10),
    ("m005", 0.05),
    ("m001", 0.01),
)
# F2.4 (2026-07-02): el fondo fijo vive en core_config (tenant-overridable
# vía caja.fondo_default / caja.fondo_por_sucursal del config.yaml). Import
# perezoso-seguro: core_config no depende de nada nuestro.
import core_config as _core_config

CAJA_FONDO_FIJO: float = _core_config.CAJA_FONDO_DEFAULT  # fallback sin sucursal

# Phase R (2026-06-05): fondo de caja distinto por sucursal
CAJA_FONDO_POR_SUCURSAL: dict[str, float] = _core_config.CAJA_FONDO_POR_SUCURSAL


def get_fondo_caja(sucursal: str | None) -> float:
    """Retorna el fondo fijo según sucursal. GYE=$100, UIO=$50, default $50."""
    if sucursal and sucursal in CAJA_FONDO_POR_SUCURSAL:
        return CAJA_FONDO_POR_SUCURSAL[sucursal]
    return CAJA_FONDO_FIJO


def calcular_cierre_caja(
    denoms: dict[str, int], sucursal: str | None = None
) -> dict[str, float]:
    """A partir de {b100:int, b50:int, ... m001:int} retorna totales y entregado.

    Cantidad es entero >=0. Si falta una key, se asume 0. Si la key viene como
    string o None, se castea a int (0 si falla).

    Fondo depende de la sucursal (Phase R 2026-06-05): GYE=$100, UIO=$50.
    """
    def _q(key: str) -> int:
        v = denoms.get(key, 0)
        if v is None or v == "":
            return 0
        try:
            return max(0, int(float(v)))
        except (TypeError, ValueError):
            return 0

    total_bil = sum(_q(k) * v for k, v in CAJA_DENOMINACIONES_BILLETES)
    total_mon = sum(_q(k) * v for k, v in CAJA_DENOMINACIONES_MONEDAS)
    total = round(total_bil + total_mon, 2)
    fondo = get_fondo_caja(sucursal)
    entregado = round(max(0.0, total - fondo), 2)
    # Detalle por denominación (para email/reporte)
    detalle_billetes = [
        {"label": label, "valor": v, "cantidad": _q(k), "subtotal": round(_q(k) * v, 2)}
        for k, v, label in [
            ("b100", 100.00, "$100"),
            ("b50", 50.00, "$50"),
            ("b20", 20.00, "$20"),
            ("b10", 10.00, "$10"),
            ("b5", 5.00, "$5"),
            ("b1", 1.00, "$1 (billete)"),
        ]
    ]
    detalle_monedas = [
        {"label": label, "valor": v, "cantidad": _q(k), "subtotal": round(_q(k) * v, 2)}
        for k, v, label in [
            ("m1", 1.00, "$1 (moneda)"),
            ("m050", 0.50, "50¢"),
            ("m025", 0.25, "25¢"),
            ("m010", 0.10, "10¢"),
            ("m005", 0.05, "5¢"),
            ("m001", 0.01, "1¢"),
        ]
    ]
    # Phase S (2026-06-08): comparar contra fondo esperado (cuadra/sobra/falta)
    diferencia = round(total - fondo, 2)
    if abs(diferencia) < 0.01:
        status = "cuadra"
        status_label = "✅ Cuadra perfecto"
    elif diferencia > 0:
        status = "sobra"
        status_label = f"⚠️ Sobra ${diferencia:,.2f}"
    else:
        status = "falta"
        status_label = f"🔴 Falta ${abs(diferencia):,.2f}"

    return {
        "total_billetes": round(total_bil, 2),
        "total_monedas": round(total_mon, 2),
        "total": total,
        "fondo": fondo,           # objetivo
        "fondo_esperado": fondo,  # alias para claridad
        "entregado": entregado,   # legacy
        "diferencia": diferencia,
        "status": status,
        "status_label": status_label,
        "detalle_billetes": detalle_billetes,
        "detalle_monedas": detalle_monedas,
    }


@_locked
def set_cierre_caja(
    user_email: str | None,
    fecha: str,
    denoms: dict[str, int],
    *,
    notas: str = "",
    sucursal: str = "",
    realizado: bool = True,
) -> dict[str, Any]:
    """Guarda el cierre de caja de una fecha específica per-user.

    Phase N (2026-06-02): info@/quito@ marcan cierre cada día 5:15 PM con las
    12 denominaciones del docx Control_Cierre_Caja. Se calcula total + entregado
    y se manda email a Daniel + Gabriela Sánchez (Mateo en CC).
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "cierres_caja" not in user:
        user["cierres_caja"] = {}

    calc = calcular_cierre_caja(denoms, sucursal=sucursal)
    user["cierres_caja"][fecha] = {
        "fecha": fecha,
        "sucursal": sucursal.strip(),
        "realizado": bool(realizado),
        "denoms": {k: max(0, int(float(denoms.get(k, 0) or 0))) for k, _ in (
            CAJA_DENOMINACIONES_BILLETES + CAJA_DENOMINACIONES_MONEDAS
        )},
        "total_billetes": calc["total_billetes"],
        "total_monedas": calc["total_monedas"],
        "total": calc["total"],
        "fondo": calc["fondo"],
        "entregado": calc["entregado"],
        "notas": notas.strip(),
        "marcado_at": _now_iso(),
    }
    save(state)
    return user["cierres_caja"][fecha]


def get_cierre_caja(
    user_email: str | None, fecha: str
) -> dict[str, Any] | None:
    """Devuelve el cierre de caja guardado para una fecha, o None."""
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    return user.get("cierres_caja", {}).get(fecha)


@_locked
def set_apertura_caja(
    user_email: str | None,
    fecha: str,
    denoms: dict[str, int],
    *,
    notas: str = "",
    sucursal: str = "",
) -> dict[str, Any]:
    """Phase S (2026-06-08): guarda apertura de caja del día (8:15 AM).

    Same shape as cierres_caja pero en `aperturas_caja[fecha]`. Permite
    comparar inicio-vs-fin del día.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "aperturas_caja" not in user:
        user["aperturas_caja"] = {}

    calc = calcular_cierre_caja(denoms, sucursal=sucursal)
    user["aperturas_caja"][fecha] = {
        "fecha": fecha,
        "sucursal": sucursal.strip(),
        "denoms": {k: max(0, int(float(denoms.get(k, 0) or 0))) for k, _ in (
            CAJA_DENOMINACIONES_BILLETES + CAJA_DENOMINACIONES_MONEDAS
        )},
        "total_billetes": calc["total_billetes"],
        "total_monedas": calc["total_monedas"],
        "total": calc["total"],
        "fondo_esperado": calc["fondo_esperado"],
        "diferencia": calc["diferencia"],
        "status": calc["status"],
        "notas": notas.strip(),
        "marcado_at": _now_iso(),
    }
    save(state)
    return user["aperturas_caja"][fecha]


def get_apertura_caja(
    user_email: str | None, fecha: str
) -> dict[str, Any] | None:
    """Devuelve la apertura de caja guardada para una fecha, o None."""
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    return user.get("aperturas_caja", {}).get(fecha)


@_locked
def set_cierre_caja_confirmacion(
    user_email: str | None,
    fecha: str,
    *,
    estado: str,
    validador: str,
    monto_recibido: float | None = None,
    razon: str = "",
) -> dict[str, Any]:
    """Marca la confirmación de un cierre de caja por el validador.

    estado: "pendiente" | "confirmado" | "discrepancia" | "no_recibido"
    Si estado=confirmado, monto_recibido = entregado (auto).
    Si estado=discrepancia, monto_recibido = lo que realmente recibió + razón.
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    cierres = user.get("cierres_caja", {})
    if fecha not in cierres:
        raise ValueError(f"No hay cierre de caja para {email} en {fecha}")

    cierre = cierres[fecha]
    if "confirmacion" not in cierre:
        cierre["confirmacion"] = {
            "estado": "pendiente",
            "validador": validador,
            "monto_recibido": None,
            "razon": "",
            "confirmado_at": None,
            "recordatorios": [],
        }

    confirm = cierre["confirmacion"]
    confirm["estado"] = estado
    confirm["validador"] = validador
    if estado == "confirmado":
        confirm["monto_recibido"] = cierre.get("entregado")
        confirm["razon"] = razon.strip()
    elif estado == "discrepancia":
        confirm["monto_recibido"] = monto_recibido
        confirm["razon"] = razon.strip() or "Discrepancia (sin detalle)"
    elif estado == "no_recibido":
        confirm["monto_recibido"] = 0.0
        confirm["razon"] = razon.strip() or "Pendiente de recepción"
    confirm["confirmado_at"] = _now_iso()
    save(state)
    return confirm


@_locked
def add_recordatorio_cierre(
    user_email: str | None, fecha: str
) -> None:
    """Registra un timestamp en la lista de recordatorios enviados para un cierre."""
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    cierres = user.get("cierres_caja", {})
    if fecha not in cierres:
        return
    if "confirmacion" not in cierres[fecha]:
        cierres[fecha]["confirmacion"] = {
            "estado": "pendiente", "validador": "", "monto_recibido": None,
            "razon": "", "confirmado_at": None, "recordatorios": [],
        }
    cierres[fecha]["confirmacion"]["recordatorios"].append(_now_iso())
    save(state)


def get_cierres_caja_pendientes_confirmacion() -> list[dict[str, Any]]:
    """Lista todos los cierres con confirmación pendiente (todos los users).

    Útil para el scheduler de recordatorios.
    """
    state = load()
    out: list[dict[str, Any]] = []
    for email, user_data in (state.get("users") or {}).items():
        for fecha, cierre in (user_data.get("cierres_caja") or {}).items():
            confirm = cierre.get("confirmacion")
            if not confirm or confirm.get("estado") in (None, "pendiente"):
                out.append({
                    "user_email": email,
                    "fecha": fecha,
                    "sucursal": cierre.get("sucursal", ""),
                    "entregado": cierre.get("entregado"),
                    "total": cierre.get("total"),
                    "validador": confirm.get("validador") if confirm else "",
                    "recordatorios_enviados": len(confirm.get("recordatorios", [])) if confirm else 0,
                })
    return out


# ============ Chocolates de reviews (Phase Q, 2026-06-05) ============
# Stock semanal por colaborador. Lunes setea stock_inicial. Cada día entregan
# chocolates (= reviews recibidos en Google Maps/Facebook). Cuando stock <= 5,
# alerta al colaborador.
CHOCOLATES_UMBRAL = 5


@_locked
def set_chocolates_stock_inicial(
    user_email: str | None,
    cantidad: int,
    wk: str | None = None,
) -> dict[str, Any]:
    """Setea el stock inicial de chocolates de la semana — INMUTABLE.

    Phase R (2026-06-05): si ya existe stock_inicial para la semana, NO se
    sobrescribe (first-write wins). Para corregir un error, usar reset_day
    o limpiar la semana entera.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "chocolates" not in user:
        user["chocolates"] = {}
    if wk not in user["chocolates"]:
        user["chocolates"][wk] = {
            "stock_inicial": int(cantidad),
            "entregas": {},
            "recargas": {},   # Phase R: nuevo
            "alerta_5_enviada": False,
            "creado_at": _now_iso(),
        }
    # Si ya existe, NO sobrescribir el stock_inicial — inmutable.
    save(state)
    return user["chocolates"][wk]


@_locked
def corregir_chocolates_stock(
    user_email: str | None,
    cantidad: int,
    wk: str | None = None,
) -> dict[str, Any]:
    """Corrige (override) el stock de chocolates de la semana.

    A diferencia de set_chocolates_stock_inicial (inmutable, first-write wins),
    esta fija un stock LIMPIO: stock_inicial=cantidad, entregas/recargas vacías,
    de modo que stock_actual == cantidad. Para corregir confusiones de conteo.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "chocolates" not in user:
        user["chocolates"] = {}
    user["chocolates"][wk] = {
        "stock_inicial": int(cantidad),
        "entregas": {},
        "recargas": {},
        "alerta_5_enviada": False,
        "creado_at": _now_iso(),
        "corregido_at": _now_iso(),
    }
    save(state)
    return user["chocolates"][wk]


@_locked
def add_chocolates_entrega(
    user_email: str | None,
    fecha: str,
    cantidad: int,
    wk: str | None = None,
) -> dict[str, Any]:
    """Registra una entrega de chocolates (= chocolates dados a clientes que
    dejaron review).

    Si ya había entrega previa ese día, SUMA al total.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "chocolates" not in user or wk not in user["chocolates"]:
        if "chocolates" not in user:
            user["chocolates"] = {}
        user["chocolates"][wk] = {
            # Carry-over 2026-07-17: la semana nueva arranca con el stock
            # final de la anterior (inventario físico), no en 0.
            "stock_inicial": _chocolates_prev_stock(user, wk) or 0,
            "entregas": {},
            "recargas": {},
            "alerta_5_enviada": False,
            "creado_at": _now_iso(),
        }
    rec = user["chocolates"][wk]
    rec["entregas"][fecha] = rec["entregas"].get(fecha, 0) + int(cantidad)
    save(state)
    return rec


@_locked
def add_chocolates_recarga(
    user_email: str | None,
    fecha: str,
    cantidad: int,
    wk: str | None = None,
) -> dict[str, Any]:
    """Phase R (2026-06-05): registra restock/recarga de chocolates del día.

    Cada vez que el colaborador recibe más chocolates (porque Mateo/Gabriela
    les pasó más), suma al pool. Si recibió 2 veces en el día, suma ambas.
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "chocolates" not in user or wk not in user["chocolates"]:
        if "chocolates" not in user:
            user["chocolates"] = {}
        user["chocolates"][wk] = {
            # Carry-over 2026-07-17: ver add_chocolates_entrega.
            "stock_inicial": _chocolates_prev_stock(user, wk) or 0,
            "entregas": {},
            "recargas": {},
            "alerta_5_enviada": False,
            "creado_at": _now_iso(),
        }
    rec = user["chocolates"][wk]
    if "recargas" not in rec:
        rec["recargas"] = {}
    rec["recargas"][fecha] = rec["recargas"].get(fecha, 0) + int(cantidad)
    # Si hubo recarga grande, reset alerta para que vuelva a chequear
    if int(cantidad) > 5:
        rec["alerta_5_enviada"] = False
    save(state)
    return rec


@_locked
def marcar_alerta_chocolates_enviada(
    user_email: str | None, wk: str | None = None
) -> None:
    """Marca que la alerta de stock bajo ya se envió esta semana (no spamear)."""
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "chocolates" in user and wk in user["chocolates"]:
        user["chocolates"][wk]["alerta_5_enviada"] = True
        save(state)


def _chocolates_stock_actual(rec: dict[str, Any]) -> int:
    total_e = sum(int(v) for v in (rec.get("entregas") or {}).values())
    total_r = sum(int(v) for v in (rec.get("recargas") or {}).values())
    return max(0, int(rec.get("stock_inicial", 0)) + total_r - total_e)


def _chocolates_prev_stock(user: dict[str, Any], wk: str) -> int | None:
    """Stock final de la semana de chocolates más reciente anterior a `wk`.

    Carry-over (2026-07-17): el stock de chocolates es inventario FÍSICO — no
    se esfuma el lunes. Antes cada semana arrancaba en 0 y el card volvía a
    pedir 'stock inicial' (el contador desaparecía); si el colaborador ponía
    el número en 'recarga', el contador no volvía nunca (render gateado por
    stock_inicial). None = nunca hubo datos de chocolates.
    """
    chocolates = user.get("chocolates") or {}
    try:
        target = _parse_week_key(wk)
    except (ValueError, IndexError):
        return None
    best_rec = None
    best_t: tuple[int, int] | None = None
    for k, rec in chocolates.items():
        try:
            t = _parse_week_key(k)
        except (ValueError, IndexError):
            continue
        if t < target and (best_t is None or t > best_t):
            best_t, best_rec = t, rec
    return _chocolates_stock_actual(best_rec) if best_rec is not None else None


def get_chocolates_semana(
    user_email: str | None, wk: str | None = None
) -> dict[str, Any] | None:
    """Retorna el bloque de chocolates de una semana + el stock_actual calculado.

    Si la semana no tiene registro pero una anterior sí, devuelve un bloque
    virtual con el stock arrastrado (carry-over) — el contador no desaparece
    al cambiar de semana. Si NUNCA hubo datos, retorna None (el card pide el
    stock inicial la primera vez).
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    chocolates = user.get("chocolates", {})
    wk = wk or week_key()
    rec = chocolates.get(wk)
    if not rec:
        prev = _chocolates_prev_stock(user, wk)
        if prev is None:
            return None
        return {
            "stock_inicial": prev,
            "entregas": {},
            "recargas": {},
            "alerta_5_enviada": False,
            "total_entregado": 0,
            "total_recargado": 0,
            "stock_actual": prev,
            "carryover": True,
        }
    total_entregado = sum(int(v) for v in (rec.get("entregas") or {}).values())
    total_recargado = sum(int(v) for v in (rec.get("recargas") or {}).values())
    return {
        **rec,
        "total_entregado": total_entregado,
        "total_recargado": total_recargado,
        "stock_actual": _chocolates_stock_actual(rec),
    }


# ============ TikTok seguidores semanales (Phase R, 2026-06-08) ============
# Trackear con cuántos seguidores arranca la semana en TikTok. Pregunta los
# lunes (o cualquier día si no se cargó todavía). Después de cargado, no
# se vuelve a preguntar hasta el lunes siguiente.


@_locked
def set_tiktok_seguidores_semana(
    user_email: str | None,
    cantidad: int,
    wk: str | None = None,
) -> dict[str, Any]:
    """Setea los seguidores TikTok al inicio de la semana. Sobrescribe si ya
    existe (a diferencia del stock_inicial de chocolates que es inmutable;
    los seguidores pueden corregirse si se cargó mal).
    """
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    wk = wk or week_key()
    if "tiktok_seguidores" not in user:
        user["tiktok_seguidores"] = {}
    if wk not in user["tiktok_seguidores"]:
        user["tiktok_seguidores"][wk] = {
            "seguidores": int(cantidad),
            "marcado_at": _now_iso(),
        }
    else:
        user["tiktok_seguidores"][wk]["seguidores"] = int(cantidad)
        user["tiktok_seguidores"][wk]["actualizado_at"] = _now_iso()
    save(state)
    return user["tiktok_seguidores"][wk]


def get_tiktok_seguidores_semana(
    user_email: str | None, wk: str | None = None
) -> dict[str, Any] | None:
    """Retorna el bloque de seguidores TikTok de una semana + comparativo con
    la semana anterior si existe.
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    tt = user.get("tiktok_seguidores", {})
    wk = wk or week_key()
    rec = tt.get(wk)
    if not rec:
        return None
    # Buscar la semana anterior para mostrar delta
    try:
        year_s, w_s = wk.split("-W")
        year, w = int(year_s), int(w_s)
        prev_w = w - 1
        prev_year = year
        if prev_w < 1:
            prev_w = 52
            prev_year = year - 1
        prev_wk_key = f"{prev_year:04d}-W{prev_w:02d}"
        prev_rec = tt.get(prev_wk_key)
        delta = None
        if prev_rec:
            delta = int(rec["seguidores"]) - int(prev_rec["seguidores"])
    except (ValueError, KeyError):
        delta = None
    return {
        **rec,
        "delta_vs_semana_anterior": delta,
    }


def get_cierres_caja_month(
    user_email: str | None, year: int, month: int
) -> list[dict[str, Any]]:
    """Lista cierres de un mes específico (ordenados por fecha asc)."""
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    prefix = f"{year:04d}-{month:02d}-"
    out = [
        rec
        for f, rec in (user.get("cierres_caja") or {}).items()
        if f.startswith(prefix)
    ]
    return sorted(out, key=lambda r: r["fecha"])


def daily_total(activity: dict[str, Any] | None) -> float:
    if not activity or activity.get("tipo") != "diaria":
        return 0.0
    total = 0.0
    for d in activity.get("log", {}).values():
        try:
            total += float(d.get("valor", 0) or 0)
        except (TypeError, ValueError):
            pass
    return total


def daily_compliance(
    activity: dict[str, Any] | None, *, dias_habiles: int = 5
) -> float | None:
    if not activity or activity.get("tipo") != "diaria":
        return None
    meta = activity.get("meta")
    if meta is None or meta == 0 or dias_habiles <= 0:
        return None
    expected = float(meta) * dias_habiles
    if expected == 0:
        return None
    return daily_total(activity) / expected


def list_known_users() -> list[str]:
    """Devuelve la lista de emails registrados en el state."""
    return list(load().get("users", {}).keys())


# ============ Phase U (2026-06-09): Rutas de envío para José (asistente 2 GYE) ============
# Estructura en state["users"][email]:
#   "rutas": {
#       "YYYY-MM-DD": {
#           "salidas": [
#               {"inicio_ts": "...", "fin_ts": "...|None",
#                "entregas": {factura_id: {...}}}
#           ],
#           "envios_snapshot": {factura_id: {cliente, direccion_factura,
#                                            total, items_transp, ...}}
#       }
#   }
#   "caja_chica": {
#       "inicial": float | None (first-write wins),
#       "movimientos": [
#           {"ts": "...", "tipo": "gasto|reposicion",
#            "descripcion": "...", "monto": float}
#       ]
#   }


def _today_str() -> str:
    return _today().isoformat()


def get_ruta_dia(
    user_email: str | None, fecha: str | None = None
) -> dict[str, Any]:
    """Retorna el bloque de ruta de un día (salidas + snapshot envíos).
    Si no existe, devuelve dict vacío con keys por defecto.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = state.get("users", {}).get(email, {})
    rutas = user.get("rutas", {})
    rec = rutas.get(fecha)
    if not rec:
        return {"salidas": [], "envios_snapshot": {}}
    return rec


@_locked
def set_ruta_card_id(
    user_email: str | None, fecha: str, activity_id: str
) -> None:
    """Guarda el activity_id del card de ruta del día (2026-06-23). Permite
    editar EN SU LUGAR el mismo card durante el día (un card por día) en vez de
    publicar uno nuevo en cada acción."""
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "rutas" not in user:
        user["rutas"] = {}
    if fecha not in user["rutas"]:
        user["rutas"][fecha] = {"salidas": [], "envios_snapshot": {}}
    user["rutas"][fecha]["card_activity_id"] = activity_id
    save(state)


def get_ruta_card_id(user_email: str | None, fecha: str) -> str | None:
    """Devuelve el activity_id del card de ruta del día, o None."""
    email = _normalize_email(user_email)
    state = load()
    rec = state.get("users", {}).get(email, {}).get("rutas", {}).get(fecha)
    if not rec:
        return None
    return rec.get("card_activity_id")


def prev_ruta_date_with_card(
    user_email: str | None, fecha: str
) -> str | None:
    """Devuelve la fecha (ISO) más reciente ANTERIOR a `fecha` que tenga un card
    de ruta con activity_id guardado. Para 'cerrar/contraer' el card del día
    anterior cuando arranca uno nuevo."""
    email = _normalize_email(user_email)
    state = load()
    rutas = state.get("users", {}).get(email, {}).get("rutas", {})
    candidatas = [
        f for f, rec in rutas.items()
        if f < fecha and isinstance(rec, dict) and rec.get("card_activity_id")
    ]
    return max(candidatas) if candidatas else None


@_locked
def set_envios_snapshot(
    user_email: str | None,
    envios: dict[str, dict[str, Any]],
    fecha: str | None = None,
) -> dict[str, Any]:
    """Guarda/actualiza el snapshot de envíos del día. MERGEA: si una factura
    ya estaba en el snapshot, NO la sobrescribe (preserva direccion_real /
    entrega marcadas). Solo agrega facturas nuevas.

    Llamado por el scheduler a las 11 AM y 3 PM tras consultar Contifico.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = _get_user_state(state, email)
    if "rutas" not in user:
        user["rutas"] = {}
    if fecha not in user["rutas"]:
        user["rutas"][fecha] = {"salidas": [], "envios_snapshot": {}}
    snapshot = user["rutas"][fecha].setdefault("envios_snapshot", {})
    nuevos = 0
    for fid, data in envios.items():
        if fid not in snapshot:
            snapshot[fid] = data
            nuevos += 1
    user["rutas"][fecha]["envios_snapshot"] = snapshot
    save(state)
    return {"total": len(snapshot), "nuevos": nuevos, "fecha": fecha}


@_locked
def reconcile_envios_snapshot(
    user_email: str | None,
    fresh_ids,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Quita del snapshot del día las facturas que YA NO califican como envío
    según el filtro actual (no están en `fresh_ids`), EXCEPTO:
      - las ad-hoc (agregadas a mano), y
      - las que ya tienen una entrega marcada en alguna salida del día.

    Limpia los falsos positivos VIEJOS (compras en oficina sin transporte) que
    quedaron en el snapshot por el merge histórico de set_envios_snapshot +
    carry-forward, una vez que el filtro de transporte se corrigió. Idempotente.
    Fix 2026-06-19. `set_envios_snapshot` solo agrega; ESTA es la que poda.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    fresh = set(fresh_ids or [])
    state = load()
    user = state.get("users", {}).get(email, {})
    rec = (user.get("rutas") or {}).get(fecha)
    if not rec:
        return {"removed": 0, "kept": 0}
    snap = rec.get("envios_snapshot", {}) or {}
    # Facturas con entrega marcada (cualquier salida) — preservar el trabajo hecho.
    marcadas: set[str] = set()
    for s in (rec.get("salidas") or []):
        for fid, entr in (s.get("entregas") or {}).items():
            if entr.get("status"):
                marcadas.add(fid)
    removed = 0
    for fid in list(snap.keys()):
        item = snap[fid] or {}
        if item.get("adhoc") or fid in fresh or fid in marcadas:
            continue
        del snap[fid]
        removed += 1
    if removed:
        rec["envios_snapshot"] = snap
        save(state)
    return {"removed": removed, "kept": len(snap)}


@_locked
def start_ruta(
    user_email: str | None, fecha: str | None = None
) -> dict[str, Any]:
    """Marca timestamp 'Inicio de ruta'. Crea una nueva salida abierta.

    Si ya hay una salida abierta (sin fin_ts) en el día, NO crea otra —
    retorna la abierta. Para empezar una nueva salida, primero hay que cerrar
    la anterior con end_ruta().
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = _get_user_state(state, email)
    if "rutas" not in user:
        user["rutas"] = {}
    if fecha not in user["rutas"]:
        user["rutas"][fecha] = {"salidas": [], "envios_snapshot": {}}
    salidas = user["rutas"][fecha]["salidas"]
    # Si hay una salida abierta, devolverla
    for s in salidas:
        if not s.get("fin_ts"):
            return {
                "salida_idx": salidas.index(s),
                "already_open": True,
                "inicio_ts": s["inicio_ts"],
            }
    nueva = {
        "inicio_ts": _now_iso(),
        "fin_ts": None,
        "entregas": {},
    }
    salidas.append(nueva)
    save(state)
    return {
        "salida_idx": len(salidas) - 1,
        "already_open": False,
        "inicio_ts": nueva["inicio_ts"],
    }


@_locked
def end_ruta(
    user_email: str | None,
    razones_no_entrega: dict[str, str] | None = None,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Cierra la salida abierta del día con timestamp 'Volví a la oficina'.

    razones_no_entrega: {factura_id: "por qué no se pudo entregar"} —
    se aplica a las entregas de la salida actual que NO tienen status='entregado'.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = _get_user_state(state, email)
    rutas = user.get("rutas", {})
    if fecha not in rutas:
        return {"ok": False, "msg": "no hay ruta para ese día"}
    salidas = rutas[fecha]["salidas"]
    # Buscar la última salida abierta
    abierta = None
    for s in reversed(salidas):
        if not s.get("fin_ts"):
            abierta = s
            break
    if not abierta:
        return {"ok": False, "msg": "no hay salida abierta"}
    abierta["fin_ts"] = _now_iso()
    # Aplicar razones de no-entrega
    if razones_no_entrega:
        for fid, razon in razones_no_entrega.items():
            entr = abierta["entregas"].get(fid, {})
            if entr.get("status") != "entregado":
                abierta["entregas"][fid] = {
                    **entr,
                    "status": "no_entregado",
                    "razon_no_entrega": razon,
                    "ts": _now_iso(),
                }
    save(state)
    # Calcular duración en minutos
    try:
        from datetime import datetime as _dt
        ini = _dt.fromisoformat(abierta["inicio_ts"].replace("Z", "+00:00"))
        fin = _dt.fromisoformat(abierta["fin_ts"].replace("Z", "+00:00"))
        duracion_min = int((fin - ini).total_seconds() / 60)
    except Exception:
        duracion_min = None
    return {
        "ok": True,
        "inicio_ts": abierta["inicio_ts"],
        "fin_ts": abierta["fin_ts"],
        "duracion_min": duracion_min,
        "entregas": dict(abierta["entregas"]),
    }


@_locked
def marcar_entrega(
    user_email: str | None,
    factura_id: str,
    entregado: bool,
    direccion_real: str | None = None,
    razon: str | None = None,
    pago_envio: float | None = None,
    observacion: str | None = None,
    cliente_label: str | None = None,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Phase V (2026-06-10): marca una entrega ampliada.

    - entregado=True  → status='entregado'
    - entregado=False → status='no_entregado' (requiere razon)
    - direccion_real: si la dirección de la factura NO era correcta, la real.
    - pago_envio: si José pagó por el envío al terminal, monto USD. Se resta
      automáticamente de la caja chica como gasto con descripción
      "Envío {cliente_label}".
    - observacion: texto libre.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = _get_user_state(state, email)
    rutas = user.get("rutas", {})
    if fecha not in rutas:
        return {"ok": False, "msg": "no hay ruta abierta"}
    salidas = rutas[fecha]["salidas"]
    abierta = None
    for s in reversed(salidas):
        if not s.get("fin_ts"):
            abierta = s
            break
    # Phase V (2026-06-10): permitir marcar entrega aunque NO esté en ruta —
    # se guarda en una "salida virtual" con marca de oficina.
    if not abierta:
        # Crear/usar contenedor para entregas marcadas fuera de salida
        if not salidas:
            salidas.append({
                "inicio_ts": _now_iso(),
                "fin_ts": _now_iso(),
                "marcado_en_oficina": True,
                "entregas": {},
            })
        target = salidas[-1]
        if "entregas" not in target:
            target["entregas"] = {}
        target["marcado_en_oficina"] = True
        abierta = target

    entr = {
        "status": "entregado" if entregado else "no_entregado",
        "ts": _now_iso(),
    }
    if direccion_real:
        entr["direccion_real"] = direccion_real
    if razon:
        entr["razon_no_entrega"] = razon
    if observacion:
        entr["observacion"] = observacion
    if pago_envio and pago_envio > 0:
        entr["pago_envio"] = float(pago_envio)
        # Restar de caja chica automáticamente
        desc = f"Envío {cliente_label}" if cliente_label else "Pago envío al terminal"
        if "caja_chica" not in user:
            user["caja_chica"] = {"inicial": None, "movimientos": []}
        user["caja_chica"].setdefault("movimientos", []).append({
            "ts": _now_iso(),
            "tipo": "gasto",
            "monto": float(pago_envio),
            "descripcion": desc,
            "factura_id": factura_id,
        })
    abierta["entregas"][factura_id] = entr
    save(state)
    return {"ok": True, **entr}


@_locked
def add_destino_adhoc(
    user_email: str | None,
    cliente: str,
    direccion: str,
    descripcion: str | None = None,
    tipo: str = "entrega",
    monto: float = 0.0,
    fecha: str | None = None,
) -> dict[str, Any]:
    """Phase V (2026-06-10): agrega un destino ad-hoc al snapshot del día.

    Útil cuando José tiene que ir a un lugar que NO está facturado en Contifico:
    retiros de mercadería, encargos extra, devoluciones, etc.

    tipo: 'entrega' | 'retiro' | 'otro'
    Genera un factura_id sintético `adhoc-<ts>` para diferenciarlo de las
    facturas reales de Contifico.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    state = load()
    user = _get_user_state(state, email)
    if "rutas" not in user:
        user["rutas"] = {}
    if fecha not in user["rutas"]:
        user["rutas"][fecha] = {"salidas": [], "envios_snapshot": {}}
    snap = user["rutas"][fecha].setdefault("envios_snapshot", {})

    # ID sintético basado en timestamp para no colisionar con facturas reales.
    # Garantizar unicidad: si José agrega varios destinos en el mismo segundo,
    # el truncado a 16 chars colisionaba y el segundo sobrescribía al primero.
    ts = _now_iso().replace(":", "").replace(".", "").replace("-", "")[:16]
    fid = f"adhoc-{ts}"
    _base = fid
    _n = 1
    while fid in snap:
        _n += 1
        fid = f"{_base}-{_n}"
    item = {
        "factura_id": fid,
        "documento": f"AD-HOC ({tipo.upper()})",
        "cliente": cliente,
        "direccion_factura": direccion,
        "total": float(monto),
        "fecha_emision": fecha,
        "adhoc": True,
        "tipo_adhoc": tipo,
    }
    if descripcion:
        item["descripcion_adhoc"] = descripcion
    snap[fid] = item
    save(state)
    return {"ok": True, "factura_id": fid, "item": item}


@_locked
def carry_over_envios_no_entregados(
    user_email: str | None,
    fecha_hoy: str | None = None,
    fecha_ayer: str | None = None,
) -> dict[str, Any]:
    """Phase V (2026-06-10): copia al snapshot de HOY las facturas del
    snapshot de AYER que NO fueron entregadas. Idempotente.

    Útil cuando José revisa el card el día siguiente — ve los pendientes.
    """
    email = _normalize_email(user_email)
    fecha_hoy = fecha_hoy or _today_str()
    if fecha_ayer is None:
        from datetime import date as _date_cls2, timedelta as _td
        ayer = _date_cls2.fromisoformat(fecha_hoy) - _td(days=1)
        fecha_ayer = ayer.isoformat()
    state = load()
    user = _get_user_state(state, email)
    rutas = user.get("rutas", {})
    rec_ayer = rutas.get(fecha_ayer)
    if not rec_ayer:
        return {"ok": True, "carried": 0, "msg": "sin ruta ayer"}
    snap_ayer = rec_ayer.get("envios_snapshot", {}) or {}

    # Set de facturas entregadas en cualquier salida de ayer
    entregadas_ayer: set[str] = set()
    for s in (rec_ayer.get("salidas") or []):
        for fid, entr in (s.get("entregas") or {}).items():
            if entr.get("status") == "entregado":
                entregadas_ayer.add(fid)

    # Asegurar snapshot de hoy
    if "rutas" not in user:
        user["rutas"] = {}
    if fecha_hoy not in user["rutas"]:
        user["rutas"][fecha_hoy] = {"salidas": [], "envios_snapshot": {}}
    snap_hoy = user["rutas"][fecha_hoy].setdefault("envios_snapshot", {})

    carried = 0
    for fid, data in snap_ayer.items():
        if fid in entregadas_ayer:
            continue  # ya entregada ayer, no la traemos
        if fid in snap_hoy:
            continue  # ya está en hoy
        # Marcar la fecha original para badge AYER
        item = dict(data)
        item["origen_fecha"] = data.get("fecha_emision") or fecha_ayer
        snap_hoy[fid] = item
        carried += 1
    save(state)
    return {"ok": True, "carried": carried}


def get_entregas_consolidadas_dia(
    user_email: str | None, fecha: str | None = None
) -> dict[str, dict[str, Any]]:
    """Para cada factura del snapshot del día, devuelve su estado consolidado:
    si fue entregada en alguna salida → status='entregado'.
    Si no fue entregada y hubo razón → status='no_entregado'.
    Si nunca se tocó → status='pendiente'.
    """
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    rec = get_ruta_dia(email, fecha)
    snapshot = rec.get("envios_snapshot", {}) or {}
    consolidado: dict[str, dict[str, Any]] = {}
    for fid, data in snapshot.items():
        consolidado[fid] = {
            **data,
            "status": "pendiente",
            "direccion_real": None,
            "razon_no_entrega": None,
        }
    for salida in rec.get("salidas", []):
        for fid, entr in (salida.get("entregas") or {}).items():
            cur = consolidado.setdefault(fid, {})
            # entregado siempre gana sobre no_entregado
            if entr.get("status") == "entregado":
                cur["status"] = "entregado"
                cur["entrega_ts"] = entr.get("ts")
            elif cur.get("status") != "entregado":
                cur["status"] = entr.get("status", "pendiente")
                cur["razon_no_entrega"] = entr.get(
                    "razon_no_entrega"
                ) or cur.get("razon_no_entrega")
            if entr.get("direccion_real"):
                cur["direccion_real"] = entr["direccion_real"]
            # 2026-06-15: propagar la observación y el pago que José ingresó al
            # marcar la entrega. Antes se perdían acá (quedaban solo en la
            # salida), así que ni el card ni el resumen del equipo las mostraban.
            if entr.get("observacion"):
                cur["observacion"] = entr["observacion"]
            if entr.get("pago_envio"):
                cur["pago_envio"] = entr["pago_envio"]
    return consolidado


# ----- Caja chica (José) -----


def get_caja_chica(user_email: str | None) -> dict[str, Any]:
    """Devuelve el bloque de caja chica con saldo calculado:
        {"inicial": float|None,
         "saldo": float,
         "movimientos": [...]}
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    cc = user.get("caja_chica", {})
    inicial = cc.get("inicial")
    movs = cc.get("movimientos", []) or []
    saldo = float(inicial or 0.0)
    for m in movs:
        monto = float(m.get("monto") or 0)
        if m.get("tipo") == "reposicion":
            saldo += monto
        elif m.get("tipo") == "gasto":
            saldo -= monto
    return {
        "inicial": inicial,
        "saldo": round(saldo, 2),
        "movimientos": movs,
    }


@_locked
def set_caja_chica_inicial(
    user_email: str | None, monto: float
) -> dict[str, Any]:
    """Setea el saldo INICIAL de caja chica. IMMUTABLE: si ya está seteado,
    NO se sobrescribe (first-write wins, igual que chocolates)."""
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "caja_chica" not in user:
        user["caja_chica"] = {"inicial": None, "movimientos": []}
    if user["caja_chica"].get("inicial") is None:
        user["caja_chica"]["inicial"] = float(monto)
        save(state)
        return {"ok": True, "inicial": float(monto)}
    return {
        "ok": False,
        "msg": "ya estaba seteado",
        "inicial": user["caja_chica"]["inicial"],
    }


@_locked
def add_caja_chica_movimiento(
    user_email: str | None,
    tipo: str,
    monto: float,
    descripcion: str | None = None,
) -> dict[str, Any]:
    """Agrega un gasto (tipo='gasto') o reposición (tipo='reposicion')."""
    if tipo not in ("gasto", "reposicion"):
        return {"ok": False, "msg": "tipo inválido"}
    email = _normalize_email(user_email)
    state = load()
    user = _get_user_state(state, email)
    if "caja_chica" not in user:
        user["caja_chica"] = {"inicial": None, "movimientos": []}
    mov = {
        "ts": _now_iso(),
        "tipo": tipo,
        "monto": float(monto),
        "descripcion": (descripcion or "").strip(),
    }
    user["caja_chica"].setdefault("movimientos", []).append(mov)
    save(state)
    return {"ok": True, "mov": mov}


def caja_chica_movimientos_dia(
    user_email: str | None, fecha: str | None = None
) -> list[dict[str, Any]]:
    """Devuelve solo los movimientos de un día específico (para el email
    resumen)."""
    email = _normalize_email(user_email)
    fecha = fecha or _today_str()
    cc = get_caja_chica(email)
    return [m for m in cc.get("movimientos", []) if m.get("ts", "").startswith(fecha)]


@_locked
def reset_day(user_email: str, fecha: str) -> dict[str, Any]:
    """Borra todo lo marcado en un día específico SIN tocar las activities en sí.

    Borra:
      - Cierre de caja del día
      - day_schedules[fecha]
      - log[fecha] de TODAS las activities diarias (cobranzas + otras)
      - entregas[fecha] de chocolates (no toca stock_inicial)

    NO borra:
      - Activities asignadas (cobranzas siguen apareciendo)
      - Stock inicial de chocolates de la semana
      - Marcas de otras fechas
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email)
    if not user:
        return {"ok": False, "user": email, "reason": "user no existe en state"}

    cambios = {
        "cierre_caja_borrado": False,
        "day_schedule_borrado": False,
        "marcas_diarias_borradas": 0,
        "entregas_chocolates_borradas": False,
    }

    # 1) cierre de caja
    cierres = user.get("cierres_caja") or {}
    if fecha in cierres:
        del cierres[fecha]
        cambios["cierre_caja_borrado"] = True

    # 2) day schedule
    schedules = user.get("day_schedules") or {}
    if fecha in schedules:
        del schedules[fecha]
        cambios["day_schedule_borrado"] = True

    # 3) marcas diarias en todas las semanas del user
    for wk_data in (user.get("weeks") or {}).values():
        for aid, a in (wk_data.get("activities") or {}).items():
            log = a.get("log") or {}
            if fecha in log:
                del log[fecha]
                cambios["marcas_diarias_borradas"] += 1

    # 4) entregas de chocolates (de cualquier semana que contenga la fecha)
    for wk_choco, choco in (user.get("chocolates") or {}).items():
        entregas = choco.get("entregas") or {}
        if fecha in entregas:
            del entregas[fecha]
            cambios["entregas_chocolates_borradas"] = True

    save(state)
    return {"ok": True, "user": email, "fecha": fecha, **cambios}


@_locked
def wipe_user(user_email: str) -> bool:
    """Borra TODO el state de un user (weeks, cierres_caja, day_schedules).

    Phase N+ (2026-06-02): para limpiar supervisores que se metieron al
    Activities Bot por error. NO borra el ref del bot — eso es separado.
    Retorna True si había algo que borrar.
    """
    email = _normalize_email(user_email)
    state = load()
    if email not in state.get("users", {}):
        return False
    del state["users"][email]
    save(state)
    return True


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"State path:    {STATE_PATH}")
    print(f"Default user:  {DEFAULT_USER}")
    print(f"Semana actual: {week_key()}")
    print(f"Users registrados: {list_known_users()}")

    user = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_USER
    wk_data = get_week(user)
    monday, friday = week_range(week_key())
    print(f"\n=== Semana de {user} ({monday} → {friday}) ===")
    print(f"Actividades: {len(wk_data['activities'])}")
    for aid, a in wk_data["activities"].items():
        meta = a.get("meta")
        meta_txt = f"meta={meta} {a.get('unidad','')}" if meta else "sin meta"
        print(f"  - {aid:<25} [{a['tipo']:<8}] {meta_txt}")
