"""Núcleo (engine) de la plataforma multiempresa — andamiaje aditivo.

IMPORTANTE: este paquete es NUEVO y todavía NO lo importa ningún bot/agente en
producción (`teams_bot`, `ask_agent`, `daily_report`, `azfunc/`, `core_config`).
Es la base de la migración 1→N empresas (Acciones 1-4 del
`PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md`). El comportamiento de los bots actuales
no cambia hasta que se cablee explícitamente en una fase posterior.

Regla de oro: NADA en `core/` nombra a un cliente concreto. Los valores de cada
empresa viven en `tenants/<slug>/`. El check `tools/check_core_purity.py` lo vigila.
"""
