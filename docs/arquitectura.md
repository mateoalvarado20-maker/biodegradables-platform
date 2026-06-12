# Arquitectura — Biodegradables Ecuador Platform

**Actualizado:** 2026-06-12 (post-refactor Fases 0-5).
**Complementa:** `CLAUDE.md` (operación), `AUDITORIA_TECNICA_2026-06-12.md`
(por qué el sistema es como es), `CONTRIBUTING.md` (cómo contribuir).

## Vista de runtimes

```
┌─────────────────────────────────────────────────────────────────┐
│ App Service biodegradables-bot-app (rg-biodegradables-prod)     │
│   teams_bot.py (FastAPI + Bot Framework + APScheduler)          │
│   • Data Bot (/api/messages) — gerencia: Contifico + HubSpot    │
│   • Activities Bot (/api/activities/messages) — colaboradores   │
│   • Scheduler (con lease de instancia única):                   │
│     check-ins, reminders, cobranzas, weekly/monthly/consolidado,│
│     morning_sales 8:00, [logistics si LOGISTICS_IN_BOT=1]       │
│   • Estado en /home/.claude-agent (STATE_DIR, persistente)      │
│   Deploy: tools/build_bot_package.py → az webapp deploy         │
├─────────────────────────────────────────────────────────────────┤
│ Function App func-biodegradables-ec (Consumption)               │
│   azfunc/ — GENERADO desde la raíz (tools/sync_azfunc.py)       │
│   • logistics_morning (8:00 EC) — hasta el cutover al bot       │
│   • reply_agent_tick (cada 15 min) — state en Azure Table       │
│   • triggers HTTP manuales                                      │
├─────────────────────────────────────────────────────────────────┤
│ PC de Mateo (Task Scheduler local)                              │
│   • ApolloNotifier-2hrs (única tarea activa)                    │
│   • CLIs manuales: dispatch.py, activity_tracker.py, pbi_ask.py │
│   • run_morning.bat / run_logistics.bat solo para runs manuales │
└─────────────────────────────────────────────────────────────────┘
```

## Capas de código (todas en la raíz del repo)

| Capa | Módulos | Regla |
|---|---|---|
| Infraestructura | `safe_json`, `send_ledger`, `core_config` | Sin dependencias de negocio. Todo state pasa por acá. |
| Estado de dominio | `activity_state`, `reminders`, `dispatch_state`, `reply_state`, `conversation_history` | Un módulo = un dato = un dueño. Mutadoras con `@_locked`. |
| Clientes API | `contifico_client`, `hubspot_client`, `apollo_rest`, `graph_mail`, `outlook_client`, `calendar_client`, `pbi_cloud` | Sin lógica de negocio; errores tipados (ej. `ApolloAPIError`). |
| Reportes | `daily_report`, `daily_logistics_report`, `monthly_recap`, `news_brief`, `weekly` (en bot) | Fallos críticos → no enviar + alertar; secundarios → banner. |
| Agentes IA | `ask_agent` (tools + prompts), `reply_agent` | Tools validan colaboradores; drafts con humano en el loop. |
| Apps | `teams_bot` (bot+scheduler), `azfunc/function_app` (timers) | Solo orquestación; la lógica vive en las capas de abajo. |

## Estado: quién es dueño de qué

| Dato | Dueño (runtime) | Backend | Otros accesos |
|---|---|---|---|
| `activity_state.json` | Bot (App Service) | `/home/.claude-agent` via safe_json | CLI local = OTRO universo (solo pruebas) |
| `reminders.json`, `conversation_refs.json`, `aad_lookup.json`, `conversation_history.json`, `send_ledger.json`, `scheduler_lease.json` | Bot | ídem | — |
| Dispatch (despachos OK/NO/PARCIAL) | **Azure Table `dispatchstate`** | `dispatch_state.py` backend dual | CLI local escribe a la MISMA tabla seteando `DISPATCH_TABLE_CONN` |
| Reply state (correos procesados) | **Azure Table `replystate`** | `reply_state.py` backend dual | Run local usa archivo (universo aparte — solo para --dry-run) |
| `apollo_cache.json`, `apollo_completion_state.json` | PC local (notifier) | safe_json | — |
| MSAL token cache | PC local | `msal_cache.bin` | re-auth: `python pbi_cloud.py` |

**Regla:** si un dato necesita verse desde dos runtimes → Azure Table (o un
endpoint del bot). Los archivos JSON jamás se comparten entre máquinas.

## Garantías de la plataforma (y dónde se testean)

| Garantía | Mecanismo | Test |
|---|---|---|
| Un crash nunca destruye un state | atomic write + .bak + cuarentena (`safe_json`) | `test_safe_json.py` |
| Escritores concurrentes no se pisan | locks por archivo + `@_locked` | `test_state_isolation.py` |
| Una actividad jamás cae en otro usuario | resolución AAD estricta + rechazo de no-identificados + ctx en cards | `test_identity.py`, `test_state_isolation.py` |
| Un reporte nunca sale dos veces | `send_ledger` claim/confirm | `test_delivery.py` |
| Un reporte nunca se pierde en silencio | `_reliable_job` (retry+alerta) + catch-up + misfire 1h | `test_delivery.py` |
| Reportes nunca mienten con $0 | `_safe(critical=)` + banner de datos parciales | `test_delivery.py` |
| Los horarios no se mueven por accidente | test que fija cada cron | `test_delivery.py::test_horarios_de_jobs_configurados` |
| azfunc nunca diverge de la raíz | `sync_azfunc.py --check` en CI | CI |
| Un año nuevo no calcula metas sin feriados | `core_config.holidays_for` avisa + test del año siguiente | `test_core_config.py` |

## Decisiones arquitectónicas registradas (ADR resumido)

1. **JSON endurecido en vez de base de datos (Fase 1).** SQLite/Postgres se
   evaluó; con ~7 usuarios y un solo proceso escritor, safe_json + locks da
   las mismas garantías sin migración de datos ni dependencia nueva. Revisar
   si el equipo supera ~25 usuarios o aparece un segundo proceso escritor.
2. **Rechazar usuarios no identificados en vez de adivinar (Fase 2).** Costo:
   un usuario nuevo necesita registro manual (1 comando admin). Beneficio:
   imposible contaminar datos ajenos. El incidente "Gabriela Bravo→gsanchez@"
   demostró que adivinar es más caro.
3. **Ledger de envíos en vez de "jobs cuidadosos" (Fase 3).** La idempotencia
   vive en una sola pieza testeable, no en la disciplina de cada job.
4. **Raíz como fuente única + azfunc generado (Fase 4).** Alternativa
   (paquete pip compartido) descartada por fricción de deploy en Functions;
   el sync con --check en CI da el mismo resultado con menos piezas.
5. **`core_config` para valores de negocio (Fase 5).** Los valores que el
   negocio cambia (destinatarios, metas, feriados) no deben requerir
   entender el código para cambiarse.
