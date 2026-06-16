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
