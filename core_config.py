"""core_config — configuración de negocio centralizada (Fase 5, 2026-06-12).

Antes, cada uno de estos valores vivía duplicado en 2-4 archivos (auditoría
R8/R10: para cambiar un destinatario había que tocar hasta 4 archivos; los
feriados estaban en >=4 sitios y FALTABA 2027 — en enero las metas se iban
a calcular sin feriados, silenciosamente).

Reglas:
- Este módulo es la ÚNICA fuente. Los módulos legacy lo importan y exponen
  alias con sus nombres históricos para compatibilidad.
- Los destinatarios se pueden overridear por env var SIN tocar código.
- `holidays_for(año)` AVISA FUERTE si el año no está cargado — nunca más
  un año nuevo calculando días hábiles sin feriados en silencio.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import date

logger = logging.getLogger("core_config")


# ===== Destinatarios de reportes (env-overridable) =====
def _env_list(name: str, default: str) -> list[str]:
    raw = os.environ.get(name, default)
    return [e.strip() for e in raw.split(",") if e.strip()]


# Reporte comercial diario (8 AM)
JEFE = _env_list(
    "REPORT_COMERCIAL_TO",
    "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com",
)
MIO = os.environ.get("REPORT_CC", "malvarado@biodegradablesecuador.com").strip()
# Reporte de logística diario
GABRIELA = os.environ.get(
    "REPORT_LOGISTICA_TO", "gsanchez@biodegradablesecuador.com"
).strip()

# ===== Integración de calendario (Feature 2026-06-15) =====
# Colaboradores cuyos eventos de fecha límite / reuniones el bot crea en su
# calendario de Outlook/Teams (app-only, ver graph_calendar_app.py). Por ahora
# SOLO gerencia. Requiere admin consent del permiso Application Calendars.ReadWrite
# y CALENDAR_SYNC_ENABLED=1 para que el job programado corra (ver azure_setup_checklist.md).
CALENDAR_SYNC_USERS = _env_list(
    "CALENDAR_SYNC_USERS",
    "dsanchez@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com",
)


# ===== Check-in del Activities Bot (hora Ecuador, America/Guayaquil) =====
# Única fuente de verdad de horarios y destinatarios del check-in card.
# Domingo NO hay envíos (ningún horario lo cubre).
CHECKIN_OFICINA = _env_list(
    "CHECKIN_OFICINA_USERS",
    "malvarado@biodegradablesecuador.com,gsanchez@biodegradablesecuador.com",
)
CHECKIN_SUCURSALES = _env_list(
    "CHECKIN_SUCURSALES_USERS",
    "info@biodegradablesecuador.com,quito@biodegradablesecuador.com",
)
CHECKIN_WEEKDAY_OFICINA = (16, 30)      # Lun-Vie → oficina
CHECKIN_WEEKDAY_SUCURSALES = (17, 10)   # Lun-Vie → sucursales
CHECKIN_SATURDAY_SUCURSALES = (12, 30)  # Sáb → SOLO sucursales

# Overrides puntuales por fecha ISO: ese día, el job regular del grupo omite
# a los usuarios listados aquí y en su lugar corre el horario del override.
# Las fechas pasadas se ignoran al registrar jobs (limpiar de vez en cuando).
# (El especial del 2026-06-12 16:45/16:50 se canceló — deploy pospuesto;
#  ese día producción envió con su horario normal de 17:00.)
CHECKIN_DATE_OVERRIDES: dict[str, list[tuple[tuple[int, int], list[str]]]] = {}


# ===== Timezone y horarios de jobs del scheduler (F2.2 VER-IA, 2026-07-02) =====
# ÚNICA fuente de verdad de CUÁNDO corre cada job del bot. La consumen tres
# superficies que antes podían divergir: el registro de crons en
# teams_bot._schedule_jobs, las condiciones del catch-up y el dead-man switch
# (/health/deliveries). Un tenant con otro huso u horario laboral solo edita
# el bloque `schedules:` de su config.yaml — nunca el código.
#
# Formato: {job_key: {"time": (HH, MM), "days": <dow>}} con <dow> en
# "mon-fri" | "mon-sat" | "mon" | "daily" | "mon,wed"; jobs mensuales usan
# {"time": (HH, MM), "day_of_month": N} (sin "days").
# NOTA: sin dependencias nuevas — azfunc/ comparte este módulo y no incluye
# core/ (el schema pydantic solo se importa en la rama yaml, más abajo).
TIMEZONE_NAME = os.environ.get("TENANT_TIMEZONE", "America/Guayaquil").strip()

JOB_SCHEDULES: dict[str, dict] = {
    "morning_sales":            {"time": (8, 0),   "days": "mon-sat"},
    "logistics_morning":        {"time": (8, 0),   "days": "mon-sat"},
    "auto_assign_cobranzas":    {"time": (7, 30),  "days": "mon-fri"},
    "task_confirmations":       {"time": (9, 0),   "days": "mon-fri"},
    "calendar_sync":            {"time": (8, 45),  "days": "mon-fri"},
    "daily_news_brief":         {"time": (6, 0),   "days": "daily"},
    "apertura_caja_matinal":    {"time": (8, 15),  "days": "mon-fri"},
    "consolidated_daily":       {"time": (18, 30), "days": "mon-fri"},
    "saturday_recap":           {"time": (8, 0),   "days": "mon"},
    "monthly_sales_recap":      {"time": (8, 0),   "day_of_month": 1},
    "monthly_activities_recap": {"time": (10, 0),  "day_of_month": 1},
}


# ===== Módulos activables por tenant (F2.3 VER-IA, 2026-07-02) =====
# Qué capacidades tiene contratadas el tenant. Gatean a la vez: el registro de
# jobs del scheduler, el catch-up, el dead-man (/health/deliveries) y las
# tools de los bots (ask_agent.MODULE_TOOL_NAMES). Default: TODO encendido
# (compat legacy — Biodegradables usa el catálogo completo).
#
# Dependencias entre módulos: `cobranzas` y `chofer` asientan su trabajo en el
# state de actividades y sus cards — requieren `activities` encendido (el
# registro de sus jobs exige ambos). Los endpoints /admin/trigger-* siguen
# funcionando aunque el módulo esté apagado (override manual del operador).
MODULES: dict[str, bool] = {
    "commercial": True,   # reporte comercial 8AM + recap mensual de ventas + forecasting
    "logistics":  True,   # reporte de logística (además requiere LOGISTICS_IN_BOT=1)
    "cobranzas":  True,   # auto-asignación diaria de cartera + tools de saldos
    "activities": True,   # check-ins, tareas, reminders, consolidado, recaps de actividades
    "chofer":     True,   # ruta/asistencia del chofer
    "news_brief": True,   # brief diario de noticias para el Data Bot
    "calendar":   True,   # sync de tareas a calendario (además requiere CALENDAR_SYNC_ENABLED=1)
    "marketing":  True,   # KPIs de HubSpot en el Data Bot
    # F4.3: prospección outbound (reply agent + notificador de secuencias
    # Apollo). Los jobs además requieren sus flags de cutover
    # (REPLY_AGENT_IN_BOT / APOLLO_NOTIFIER_IN_BOT).
    "prospecting": True,
}


def module_enabled(name: str) -> bool:
    """¿El tenant tiene este módulo? Nombre desconocido = error de programa."""
    return MODULES[name]


def _apply_module_overrides(cfg_modules: dict) -> None:
    """Aplica el bloque `modules:` del tenant sobre MODULES (fail-closed)."""
    for key, enabled in cfg_modules.items():
        if key not in MODULES:
            raise ValueError(
                f"modules.{key} no es un módulo conocido; válidos: {sorted(MODULES)}"
            )
        MODULES[key] = bool(enabled)


def _apply_schedule_overrides(cfg_schedules: dict) -> None:
    """Aplica el bloque `schedules:` del tenant sobre JOB_SCHEDULES.

    Fail-closed: una clave desconocida o una combinación inválida
    (days en un job mensual y viceversa) es un error de onboarding que debe
    detener el arranque, no ignorarse. Cada entry expone .time ("HH:MM"),
    .days y .day_of_month (el schema pydantic de core/config/schema.py).
    """
    for key, entry in cfg_schedules.items():
        if key not in JOB_SCHEDULES:
            raise ValueError(
                f"schedules.{key} no es un job conocido; válidos: "
                f"{sorted(JOB_SCHEDULES)}"
            )
        sched = dict(JOB_SCHEDULES[key])
        hh_mm = str(entry.time).split(":")
        sched["time"] = (int(hh_mm[0]), int(hh_mm[1]))
        if entry.days is not None:
            if "day_of_month" in sched:
                raise ValueError(
                    f"schedules.{key}: es un job mensual (day_of_month), no acepta days"
                )
            sched["days"] = entry.days
        if entry.day_of_month is not None:
            if "day_of_month" not in sched:
                raise ValueError(
                    f"schedules.{key}: no es un job mensual, no acepta day_of_month"
                )
            sched["day_of_month"] = entry.day_of_month
        JOB_SCHEDULES[key] = sched


# ===== Meta comercial =====
# Meta del mes = ventas del mismo mes año anterior × META_FACTOR
META_FACTOR = float(os.environ.get("META_FACTOR", "1.20"))

# Override de "ventas mismo mes año anterior" cuando Contifico difiere de lo
# que el negocio considera correcto (facturas anuladas mal clasificadas, etc.)
# Fase 5 (fix R8): keyed por (año_actual, mes) — el override de mayo 2026 ya
# NO se re-aplica silenciosamente en mayo 2027/2028.
PY_OVERRIDE: dict[tuple[int, int], float] = {
    (2026, 5): 38000.0,  # mayo 2026: usuario reporta $38K (PBI mostraba $33,956)
}


def py_override_for(year: int, month: int) -> float | None:
    """Override del PY para el mes (year, month) = el mes del REPORTE actual."""
    return PY_OVERRIDE.get((year, month))


# ===== Umbrales semáforo =====
CUMPL_VERDE = 1.00     # >= 100% del cumplimiento esperado a hoy
CUMPL_AMARILLO = 0.85  # 85-99% amarillo, < 85% rojo
AYER_VERDE = 1.00      # ayer >= meta diaria base
AYER_AMARILLO = 0.80   # 80-99% amarillo, < 80% rojo
MORA_VERDE = 0.05      # mora < 5% verde
MORA_AMARILLO = 0.10   # 5-10% amarillo, > 10% rojo


# ===== Feriados Ecuador =====
# Mantener al menos el año actual y el siguiente. Las fechas con (T) son
# traslados oficiales ya confirmados; las de años futuros son NOMINALES —
# verificar los traslados cuando el Ministerio de Trabajo los publique.
EC_HOLIDAYS: dict[int, list[date]] = {
    2025: [
        date(2025, 1, 1),    # Año Nuevo
        date(2025, 3, 3),    # Carnaval (lunes)
        date(2025, 3, 4),    # Carnaval (martes)
        date(2025, 4, 18),   # Viernes Santo
        date(2025, 5, 1),    # Día del Trabajo
        date(2025, 5, 23),   # Batalla de Pichincha (T, del 24)
        date(2025, 8, 10),   # Primer Grito de Independencia
        date(2025, 10, 10),  # Independencia de Guayaquil (T, del 9)
        date(2025, 11, 3),   # Difuntos / Independencia de Cuenca
        date(2025, 12, 25),  # Navidad
    ],
    2026: [
        date(2026, 1, 1),    # Año Nuevo
        date(2026, 2, 16),   # Carnaval (lunes)
        date(2026, 2, 17),   # Carnaval (martes)
        date(2026, 4, 3),    # Viernes Santo
        date(2026, 5, 1),    # Día del Trabajo
        date(2026, 5, 25),   # Batalla de Pichincha (T, lunes)
        date(2026, 8, 10),   # Primer Grito de Independencia
        date(2026, 10, 9),   # Independencia de Guayaquil
        date(2026, 11, 2),   # Día de los Difuntos
        date(2026, 11, 3),   # Independencia de Cuenca
        date(2026, 12, 25),  # Navidad
    ],
    2027: [
        # NOMINALES (Fase 5 — antes 2027 directamente NO EXISTÍA y enero iba
        # a calcular días hábiles sin feriados). ⚠️ VERIFICAR traslados
        # oficiales del Ministerio de Trabajo cuando se publiquen.
        date(2027, 1, 1),    # Año Nuevo (viernes)
        date(2027, 2, 8),    # Carnaval (lunes)
        date(2027, 2, 9),    # Carnaval (martes)
        date(2027, 3, 26),   # Viernes Santo
        date(2027, 5, 1),    # Día del Trabajo (sábado)
        date(2027, 5, 24),   # Batalla de Pichincha (lunes)
        date(2027, 8, 10),   # Primer Grito (martes — probable T al lun 9)
        date(2027, 10, 9),   # Independencia de Guayaquil (sábado)
        date(2027, 11, 2),   # Difuntos (martes — probable T)
        date(2027, 11, 3),   # Independencia de Cuenca (miércoles)
        date(2027, 12, 25),  # Navidad (sábado)
    ],
}

_warned_years: set[int] = set()


def holidays_for(year: int) -> set[date]:
    """Feriados del año. Si el año NO está cargado, avisa FUERTE (log ERROR +
    stderr) en vez de devolver vacío en silencio — los días hábiles y las
    metas diarias saldrían mal todo el año sin que nadie lo note."""
    days = EC_HOLIDAYS.get(year)
    if days is None:
        if year not in _warned_years:
            _warned_years.add(year)
            msg = (
                f"⚠️ EC_HOLIDAYS NO tiene el año {year} — los días hábiles se "
                "están calculando SIN feriados. Agregar el año en core_config.py."
            )
            logger.error(msg)
            print(f"[core_config] {msg}", file=sys.stderr)
        return set()
    return set(days)


# ===== Identidad de empresa y directorio de personas (Fase 1 demo/multiempresa) =====
# Antes, todo esto vivía hardcodeado en ask_agent.py / daily_report.py /
# news_brief.py (nombres reales de personas, empresa, sucursales). Ahora es
# config: los valores DEFAULT son idénticos a los históricos de Biodegradables
# (lo fija test_tenant_config_biodegradables), y se reemplazan por los del tenant
# cuando TENANT_CONFIG_SOURCE=yaml (ver _maybe_load_from_tenant).
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Biodegradables Ecuador").strip()
COMPANY_SECTOR = os.environ.get(
    "COMPANY_SECTOR", "distribución de productos biodegradables"
).strip()
# Texto corto de sucursales para los prompts ("Quito (UIO) y Guayaquil (GYE)").
COMPANY_SUCURSALES_DESC = os.environ.get(
    "COMPANY_SUCURSALES_DESC", "Quito (UIO) y Guayaquil (GYE)"
).strip()

# Nombre legible de cada código de sucursal.
SUCURSAL_NAMES: dict[str, str] = {"UIO": "Quito", "GYE": "Guayaquil"}

# ===== Identidad outbound + parámetros de negocio (F2.4 VER-IA, 2026-07-02) =====
# Valores que estaban horneados en reply_agent, apollo_completion_notifier,
# contifico_client, daily_logistics_report, activity_state y daily_report.
# Defaults = Biodegradables (legacy); el YAML del tenant los reemplaza.
COMPANY_DOMAIN = os.environ.get("COMPANY_DOMAIN", "biodegradablesecuador.com").strip()
COMPANY_WEBSITE = os.environ.get(
    "COMPANY_WEBSITE", "https://www.biodegradablesecuador.com/"
).strip()
# Quién firma los correos salientes a prospectos (reply agent).
OUTBOUND_SIGNER_EMAIL = os.environ.get(
    "OUTBOUND_SIGNER_EMAIL", "malvarado@biodegradablesecuador.com"
).strip()


def outbound_signer_name() -> str:
    """Nombre humano del firmante outbound, desde el directorio PEOPLE."""
    p = PEOPLE.get(OUTBOUND_SIGNER_EMAIL.lower(), {})
    return p.get("name") or OUTBOUND_SIGNER_EMAIL.split("@")[0].title()


# Prefijos de documento del ERP por sucursal de emisión (Contifico:
# "001-001-000123" → GYE). Los usan contifico_client, daily_logistics_report
# y los prompts del Data Bot.
DOC_PREFIXES: dict[str, str] = {"GYE": "001-001", "UIO": "001-002"}

# Link al dashboard del cliente en el footer del reporte comercial.
DASHBOARD_URL = os.environ.get(
    "DASHBOARD_URL",
    "https://app.powerbi.com/groups/me/reports/de5387d4-8203-4a93-8eaf-04212041fece",
).strip()

# Fondo fijo de caja por sucursal (cierre de caja de asistentes).
CAJA_FONDO_DEFAULT: float = 50.00
CAJA_FONDO_POR_SUCURSAL: dict[str, float] = {"Guayaquil": 100.00, "Quito": 50.00}

# Keywords de provincia/ciudad para parsear direcciones en logística.
# VACÍO = usar el dataset default de Ecuador que vive en
# daily_logistics_report.PROVINCIA_KEYWORDS_EC. Un tenant de otro país lo
# reemplaza completo desde su YAML (logistics.provincia_keywords).
LOGISTICS_PROVINCIA_KEYWORDS: list[tuple[str, str, str]] = []

# Forma canónica de una persona: todos los atributos presentes (con defaults).
# Normalizar garantiza que el PEOPLE legacy y el cargado desde YAML tengan la
# MISMA forma (lo verifica test_tenant_config_biodegradables).
_PEOPLE_DEFAULTS: dict = {
    "name": "", "role": "colaborador", "sucursal": None,
    "asistente_num": None, "supervisor": False, "rotativo_sabado": False,
}


def _normalize_people(raw: dict[str, dict]) -> dict[str, dict]:
    return {
        e.strip().lower(): {**_PEOPLE_DEFAULTS, **attrs}
        for e, attrs in raw.items()
    }


# Directorio de personas. Clave = email (lower). Atributos:
#   name           nombre humano
#   role           gerente_general | gerente_comercial | analista | asistente | chofer
#   sucursal       "UIO" | "GYE" | None (oficina / sin sucursal)
#   asistente_num  1 | 2 | None  (numeración del rótulo "Asistente N")
#   supervisor     bool — supervisa al equipo, NO trackea actividades propias
#   rotativo_sabado bool — entra en la rotación de sábados de su sucursal
_PEOPLE_RAW: dict[str, dict] = {
    "dsanchez@biodegradablesecuador.com": {
        "name": "Daniel Sánchez", "role": "gerente_general",
        "sucursal": None, "supervisor": True,
    },
    "gsanchez@biodegradablesecuador.com": {
        "name": "Gabriela Sánchez", "role": "gerente_comercial", "sucursal": None,
    },
    "malvarado@biodegradablesecuador.com": {
        "name": "Mateo Alvarado", "role": "analista", "sucursal": None,
    },
    "info@biodegradablesecuador.com": {
        "name": "Gabriela Bravo", "role": "asistente", "sucursal": "GYE",
        "asistente_num": 1, "rotativo_sabado": True,
    },
    "quito@biodegradablesecuador.com": {
        "name": "Gladys López", "role": "asistente", "sucursal": "UIO",
        "asistente_num": 1,
    },
    "jsolorzano@biodegradablesecuador.com": {
        "name": "José Solórzano", "role": "chofer", "sucursal": "GYE",
        "asistente_num": 2, "rotativo_sabado": True,
    },
}
PEOPLE: dict[str, dict] = _normalize_people(_PEOPLE_RAW)


def _person(email: str | None) -> dict:
    return PEOPLE.get((email or "").strip().lower(), {})


def display_name_for(email: str | None) -> str:
    """Nombre con rótulo de asistente entre paréntesis, igual al EMAIL_TO_NAME
    histórico: 'Gabriela Bravo (Asistente 1 GYE)'. Personas sin sucursal devuelven
    el nombre a secas; emails desconocidos, ''."""
    p = _person(email)
    if not p:
        return ""
    name = p["name"]
    num, suc = p.get("asistente_num"), p.get("sucursal")
    if num and suc:
        return f"{name} (Asistente {num} {suc})"
    return name


def sucursal_for(email: str | None) -> str | None:
    return _person(email).get("sucursal")


def sucursal_name_for(email: str | None) -> str:
    return SUCURSAL_NAMES.get(sucursal_for(email) or "", "")


def role_for(email: str | None) -> str | None:
    return _person(email).get("role")


def gerente_general_name() -> str:
    for p in PEOPLE.values():
        if p.get("role") == "gerente_general":
            return p["name"]
    return ""


def email_by_role(role: str) -> str:
    """Primer email con ese role ('gerente_general', 'gerente_comercial',
    'analista'...). '' si no hay."""
    return next((e for e, p in PEOPLE.items() if p.get("role") == role), "")


def asistente_email_for_sucursal(sucursal: str) -> str:
    """Email del asistente (num 1) de una sucursal ('UIO'/'GYE'). '' si no hay."""
    return next(
        (e for e, p in PEOPLE.items()
         if p.get("role") == "asistente" and p.get("sucursal") == sucursal),
        "",
    )


def chofer_email() -> str:
    return next((e for e, p in PEOPLE.items() if p.get("role") == "chofer"), "")


def email_domain() -> str:
    """Dominio de correo del tenant, deducido de los destinatarios conocidos.
    Para sufijar pseudo-emails de usuarios sin identificar con el dominio CORRECTO
    (no el del cliente real). '' si no se puede deducir."""
    for e in [*JEFE, MIO, *PEOPLE.keys()]:
        if e and "@" in e:
            return e.split("@", 1)[1]
    return ""


def _build_identity() -> None:
    """(Re)deriva los conjuntos de identidad desde PEOPLE. Llamado al import y
    después de cargar el tenant (yaml)."""
    global EMAIL_TO_NAME, SUPERVISORS_ONLY_EMAILS, ASISTENTE_EMAILS
    global CHOFER_EMAILS, ROTATIVOS_SABADO_EMAILS
    EMAIL_TO_NAME = {e: display_name_for(e) for e in PEOPLE}
    SUPERVISORS_ONLY_EMAILS = {e for e, p in PEOPLE.items() if p.get("supervisor")}
    ASISTENTE_EMAILS = {e for e, p in PEOPLE.items() if p.get("role") == "asistente"}
    CHOFER_EMAILS = {e for e, p in PEOPLE.items() if p.get("role") == "chofer"}
    ROTATIVOS_SABADO_EMAILS = {e for e, p in PEOPLE.items() if p.get("rotativo_sabado")}


# Inicializa los derivados (se recalculan en _maybe_load_from_tenant si yaml).
EMAIL_TO_NAME: dict[str, str] = {}
SUPERVISORS_ONLY_EMAILS: set[str] = set()
ASISTENTE_EMAILS: set[str] = set()
CHOFER_EMAILS: set[str] = set()
ROTATIVOS_SABADO_EMAILS: set[str] = set()
_build_identity()


# ===== Switch multiempresa (opt-in, default LEGACY) ====================
# Fase F0/F1 del plan multiempresa (ver PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md).
#
# Por defecto NO cambia NADA: todos los valores de arriba son la fuente. Si y solo
# si la env var TENANT_CONFIG_SOURCE=yaml, los valores se REEMPLAZAN por los del
# paquete del tenant (tenants/<slug>/config.yaml, slug = TENANT_SLUG o
# "biodegradables"). La equivalencia legacy == yaml está fijada por
# tests/test_tenant_config_biodegradables.py y tests/test_core_config_switch.py.
#
# El import de `core/` es PEREZOSO (solo dentro de la rama yaml), así que el camino
# legacy no depende de pydantic/pyyaml ni del paquete core/ — clave para que
# azfunc/ (que no incluye core/) siga funcionando con el flag ausente.
def _maybe_load_from_tenant() -> None:
    if os.environ.get("TENANT_CONFIG_SOURCE", "legacy").strip().lower() != "yaml":
        return
    global JEFE, MIO, GABRIELA, CALENDAR_SYNC_USERS
    global CHECKIN_OFICINA, CHECKIN_SUCURSALES
    global CHECKIN_WEEKDAY_OFICINA, CHECKIN_WEEKDAY_SUCURSALES, CHECKIN_SATURDAY_SUCURSALES
    global META_FACTOR, PY_OVERRIDE, EC_HOLIDAYS
    global CUMPL_VERDE, CUMPL_AMARILLO, AYER_VERDE, AYER_AMARILLO, MORA_VERDE, MORA_AMARILLO
    global COMPANY_NAME, COMPANY_SECTOR, COMPANY_SUCURSALES_DESC, SUCURSAL_NAMES, PEOPLE
    global TIMEZONE_NAME
    global COMPANY_DOMAIN, COMPANY_WEBSITE, OUTBOUND_SIGNER_EMAIL
    global DOC_PREFIXES, DASHBOARD_URL
    global CAJA_FONDO_DEFAULT, CAJA_FONDO_POR_SUCURSAL, LOGISTICS_PROVINCIA_KEYWORDS

    from core.config.loader import load_tenant_config
    from core.config.schema import parse_hhmm

    slug = os.environ.get("TENANT_SLUG", "biodegradables")
    cfg = load_tenant_config(slug)

    r = cfg.recipients
    JEFE = list(r.commercial_report)
    if r.commercial_report_cc:
        MIO = r.commercial_report_cc[0]
    if r.logistics_report:
        GABRIELA = r.logistics_report[0]
    CALENDAR_SYNC_USERS = list(r.calendar_sync_users)

    CHECKIN_OFICINA = list(cfg.checkin.oficina.users)
    CHECKIN_SUCURSALES = list(cfg.checkin.sucursales.users)
    CHECKIN_WEEKDAY_OFICINA = parse_hhmm(cfg.checkin.oficina.weekday_time) or CHECKIN_WEEKDAY_OFICINA
    CHECKIN_WEEKDAY_SUCURSALES = (
        parse_hhmm(cfg.checkin.sucursales.weekday_time) or CHECKIN_WEEKDAY_SUCURSALES
    )
    CHECKIN_SATURDAY_SUCURSALES = (
        parse_hhmm(cfg.checkin.sucursales.saturday_time) or CHECKIN_SATURDAY_SUCURSALES
    )

    META_FACTOR = cfg.commercial.meta_factor
    PY_OVERRIDE = cfg.py_override_map()

    t = cfg.commercial.thresholds
    CUMPL_VERDE = t.cumpl_verde
    CUMPL_AMARILLO = t.cumpl_amarillo
    AYER_VERDE = t.ayer_verde
    AYER_AMARILLO = t.ayer_amarillo
    MORA_VERDE = t.mora_verde
    MORA_AMARILLO = t.mora_amarillo

    EC_HOLIDAYS = {year: list(days) for year, days in cfg.holidays.items()}

    # Timezone + horarios de jobs (F2.2) y módulos contratados (F2.3).
    TIMEZONE_NAME = cfg.timezone or TIMEZONE_NAME
    _apply_schedule_overrides(cfg.schedules)
    _apply_module_overrides(cfg.modules)

    # Identidad outbound + parámetros de negocio (F2.4).
    if cfg.company.domain:
        COMPANY_DOMAIN = cfg.company.domain
    if cfg.company.website:
        COMPANY_WEBSITE = cfg.company.website
    if cfg.company.outbound_signer:
        OUTBOUND_SIGNER_EMAIL = cfg.company.outbound_signer
    if cfg.erp.document_prefixes:
        DOC_PREFIXES = dict(cfg.erp.document_prefixes)
    if cfg.commercial.dashboard_url:
        DASHBOARD_URL = cfg.commercial.dashboard_url
    if cfg.caja.fondo_default is not None:
        CAJA_FONDO_DEFAULT = float(cfg.caja.fondo_default)
    if cfg.caja.fondo_por_sucursal:
        CAJA_FONDO_POR_SUCURSAL = {
            k: float(v) for k, v in cfg.caja.fondo_por_sucursal.items()
        }
    if cfg.logistics.provincia_keywords:
        LOGISTICS_PROVINCIA_KEYWORDS = [
            (str(kw), str(prov), str(ciudad))
            for kw, prov, ciudad in cfg.logistics.provincia_keywords
        ]

    # Identidad de empresa + directorio de personas (Fase 1).
    COMPANY_NAME = cfg.display_name
    if cfg.company.sector:
        COMPANY_SECTOR = cfg.company.sector
    if cfg.company.sucursales_desc:
        COMPANY_SUCURSALES_DESC = cfg.company.sucursales_desc
    if cfg.company.sucursal_names:
        SUCURSAL_NAMES = dict(cfg.company.sucursal_names)
    if cfg.people:
        PEOPLE = _normalize_people({
            p.email: {
                "name": p.name,
                "role": p.role,
                "sucursal": p.sucursal,
                "asistente_num": p.asistente_num,
                "supervisor": p.supervisor,
                "rotativo_sabado": p.rotativo_sabado,
            }
            for p in cfg.people
        })
    _build_identity()
    logger.info("core_config: valores cargados desde tenants/%s/config.yaml", slug)


_maybe_load_from_tenant()
