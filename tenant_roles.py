"""tenant_roles — aliases de identidad/roles del tenant (F4.4a VER-IA).

Derivaciones PURAS de core_config (single source, tenant-overridable):
qué emails cumplen qué rol operativo (asistentes, chofer, validadores,
supervisores). Extraído de teams_bot.py el 2026-07-04 para que los builders
de cards (bot_cards) y el bot los compartan SIN ciclos de import.

Regla de capas: este módulo importa SOLO core_config.
"""
from __future__ import annotations

import core_config

# ===== Proactive messaging (check-in del activities bot) =====
# Emails que tienen horarios CUSTOM y NO entran en el send_daily_checkin general
# Identidad/roles desde core_config (single source, tenant-overridable).
INFO_EMAIL = core_config.asistente_email_for_sucursal("GYE")
QUITO_EMAIL = core_config.asistente_email_for_sucursal("UIO")
JOSE_EMAIL = core_config.chofer_email()  # chofer GYE
# Horarios/destinatarios del check-in viven en core_config (CHECKIN_*).

# Phase N (2026-06-02): cierre de caja diario en sub-card del check-in
CIERRE_CAJA_USERS = {INFO_EMAIL, QUITO_EMAIL}
SUCURSAL_POR_USER = {
    INFO_EMAIL: core_config.sucursal_name_for(INFO_EMAIL),
    QUITO_EMAIL: core_config.sucursal_name_for(QUITO_EMAIL),
    JOSE_EMAIL: core_config.sucursal_name_for(JOSE_EMAIL),
}

# Phase U (2026-06-09): usuarios que reciben el card de ruta de envíos
# (no el check-in normal de actividades). Por ahora solo José en GYE.
ROUTE_USERS: set[str] = {JOSE_EMAIL}

# Phase P (2026-06-05): validador del efectivo entregado en cada sucursal.
# Cuando info@/quito@ guarda su cierre, el validador correspondiente recibe
# un card proactivo para confirmar que recibió el monto reportado.
VALIDADOR_CIERRE_POR_CIUDAD = {
    # El gerente general valida el cierre de la sucursal del chofer (GYE);
    # el gerente comercial valida la otra (UIO). Desde core_config.
    core_config.SUCURSAL_NAMES.get("GYE", "Guayaquil"): core_config.email_by_role("gerente_general"),
    core_config.SUCURSAL_NAMES.get("UIO", "Quito"): core_config.email_by_role("gerente_comercial"),
}

# Supervisores que NO trackean actividades propias — solo reciben los reportes
# de los colaboradores. Se excluyen del send_daily_checkin y NO se les crean
# actividades aunque haya un ref del bot por accidente. Desde core_config (debe
# matchear ask_agent.SUPERVISORS_ONLY_EMAILS).
SUPERVISORS_ONLY: set[str] = set(core_config.SUPERVISORS_ONLY_EMAILS)

# Quienes pueden consultar la carga de TODO el equipo vía /tareas (gerencia).
# El gerente comercial SÍ trackea actividades propias (no está en SUPERVISORS_ONLY)
# pero puede ver la carga del equipo.
WORKLOAD_SUPERVISORS: set[str] = SUPERVISORS_ONLY | {
    core_config.email_by_role("gerente_comercial"),
}

# Phase U: el resumen del día del chofer va a gerencia general + analista
# (el gerente comercial maneja UIO, el chofer es GYE).
JOSE_SUMMARY_TO = [
    core_config.email_by_role("gerente_general"),
    core_config.email_by_role("analista"),
]
