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


def _get_user_state(state: dict, user_email: str) -> dict[str, Any]:
    email = _normalize_email(user_email)
    if email not in state["users"]:
        state["users"][email] = {"weeks": {}}
    return state["users"][email]


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

    activities: dict[str, dict[str, Any]] = {}
    for a in template["activities"]:
        entry: dict[str, Any] = {
            "nombre": a["nombre"],
            "tipo": a["tipo"],
            "meta": a.get("meta"),
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
        activities[a["id"]] = entry

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

    entry["avance"] = avance
    if notas:
        entry["notas"] = notas
    entry["ultima_actualizacion"] = _now_iso()
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
    wk: str | None = None,
) -> dict[str, Any]:
    if tipo not in VALID_TIPOS:
        raise ValueError(f"tipo inválido: {tipo}")
    if fuente not in VALID_FUENTES:
        raise ValueError(f"fuente inválida: {fuente}")

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

    activities[activity_id] = entry
    save(state)
    return entry


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
    del activities[activity_id]
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
CAJA_FONDO_FIJO: float = 50.00  # fallback si no se conoce sucursal

# Phase R (2026-06-05): fondo de caja distinto por sucursal
CAJA_FONDO_POR_SUCURSAL: dict[str, float] = {
    "Guayaquil": 100.00,
    "Quito": 50.00,
}


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
            "stock_inicial": 0,
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
            "stock_inicial": 0,
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


def get_chocolates_semana(
    user_email: str | None, wk: str | None = None
) -> dict[str, Any] | None:
    """Retorna el bloque de chocolates de una semana + el stock_actual calculado.

    Si no hay registro, retorna None.
    """
    email = _normalize_email(user_email)
    state = load()
    user = state.get("users", {}).get(email, {})
    chocolates = user.get("chocolates", {})
    wk = wk or week_key()
    rec = chocolates.get(wk)
    if not rec:
        return None
    total_entregado = sum(int(v) for v in (rec.get("entregas") or {}).values())
    total_recargado = sum(int(v) for v in (rec.get("recargas") or {}).values())
    stock_actual = max(
        0,
        int(rec.get("stock_inicial", 0)) + total_recargado - total_entregado,
    )
    return {
        **rec,
        "total_entregado": total_entregado,
        "total_recargado": total_recargado,
        "stock_actual": stock_actual,
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

    # ID sintético basado en timestamp para no colisionar con facturas reales
    ts = _now_iso().replace(":", "").replace(".", "").replace("-", "")[:16]
    fid = f"adhoc-{ts}"
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
