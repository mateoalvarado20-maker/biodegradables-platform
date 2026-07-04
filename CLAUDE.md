# Proyecto: Plataforma VER-IA (tenant #1: Biodegradables Ecuador)

**Desde 2026-07-02 este repo es el producto de VER-IA**: una plataforma SaaS
de agentes IA, bots de Teams y automatización empresarial, multi-tenant por
diseño. **Biodegradables Ecuador es el tenant #1** (primer cliente), descrito
íntegramente por `tenants/biodegradables/` (config.yaml + integrations.yaml +
prompts). Historia del pivot y fases: memoria `project_veria_pivot`.

**Si eres Claude en una sesión nueva:** lee este archivo antes de hacer
cambios. OJO: puede haber OTRA sesión de Claude trabajando en paralelo en
esta PC — `git fetch` antes de basar trabajo en master, y trabaja desde el
worktree `C:\Users\Mateo\.worktrees\f0-estabilizacion` (no el árbol principal).

## ⚡ PLATAFORMA VER-IA — reglas vigentes (2026-07-04)

- **La fuente de verdad de la config del tenant es `tenants/<slug>/config.yaml`**
  (producción corre con `TENANT_CONFIG_SOURCE=yaml` + `TENANT_SLUG`): destinatarios,
  personas/roles, horarios de TODOS los jobs (`schedules:`), timezone, módulos
  contratados (`modules:`), metas/umbrales/feriados, prefijos ERP, caja,
  branding. `core_config.py` conserva defaults legacy (los usa azfunc/, que
  muere en F4.3b). `integrations.yaml` declara secrets (fuente keyvault|env),
  nunca valores.
- **Todo cambio va por PR con el CI en verde** (branch protection activa en
  master). Gates: ruff, drift azfunc, higiene async (teams_bot + admin_api),
  validate_tenant, pytest (351+), build del paquete (con gate de imports vía
  AST), docker build + smoke.
- **Capas fijadas por test** (dirección única): `teams_bot` → `admin_api`/
  `bot_cards`/`team_reports` → `tenant_roles` → `core_config`. Reportes de
  equipo van en `team_reports`, cards en `bot_cards`, endpoints admin en
  `admin_api` — tests de umbral impiden que `teams_bot`/`ask_agent` vuelvan
  a engordar.
- **Metering de IA (F3)**: todo `messages.create` nuevo debe registrar
  `llm_usage.record(agente, MODEL, resp.usage)`; modelo sin precio en
  `llm_usage.PRICES_USD_PER_MTOK` rompe CI. Consulta: `GET /admin/llm-usage`
  o `python llm_usage.py status`. Presupuesto: `LLM_BUDGET_MONTHLY_USD`.
- **Dependencias**: el zip deploya `requirements.txt` (RAÍZ) — agregar
  paquetes runtime a AMBOS (raíz + `requirements_bot.txt`); test lo exige.
- **JAMÁS editar archivos del repo con PowerShell** (Get/Set-Content =
  mojibake+BOM). Solo Edit tool o scripts Python. Commits largos: `git
  commit -F archivo` (comillas dobles en here-strings rompen el quoting).
- Deploy del bot: `python tools/build_bot_package.py` → `az webapp deploy`
  (desde el worktree en master actualizado). Post-deploy verificar `/health`,
  `/health/deliveries` (dead-man) y, si se agregaron jobs con ledger key
  nueva, sembrar las claves del día ANTES del deploy (vía Kudu VFS) para que
  el catch-up no re-envíe cards.
- Aprovisionamiento de un cliente nuevo: `ops/provision_tenant.py <slug>`
  (App Regs + Bots + App Service + Key Vault) → cargar secrets → consent →
  `ops/gen_teams_app.py` → deploy. Validación: `ops/validate_tenant.py`.

---

## ✅ Refactor 2026-06-12 + estado operativo

Las 6 fases del refactor post-auditoría están implementadas y commiteadas
(`git log`). Diagnóstico original: `AUDITORIA_TECNICA_2026-06-12.md`.
Documentación viva: `CONTRIBUTING.md` (reglas + PR checklist),
`docs/arquitectura.md` (capas, dueños de estado, garantías y dónde se
testean), `docs/onboarding.md`, `docs/runbook-operativo.md` (incidentes).

**Reglas permanentes:**
- El proyecto es un **repo git** (`C:\Users\Mateo`, `.gitignore` whitelist). Todo cambio se commitea (idealmente por PR con el CI de `.github/workflows/ci.yml`).
- **NO editar `azfunc\` a mano** (salvo los archivos azfunc-específicos listados en `tools/sync_azfunc.py`): se GENERA con `python tools/sync_azfunc.py`. El zip del bot se genera con `python tools/build_bot_package.py`.
- Todo state pasa por `safe_json` (atómico+backup+cuarentena+locks); todo envío programado pasa por `_reliable_job` + `send_ledger` (nunca dos veces, nunca perdido en silencio); identidad SOLO desde el registro AAD (display name prohibido como fuente); config de negocio (destinatarios, feriados, PY_OVERRIDE, umbrales) SOLO en `core_config.py`/env vars; `date.today()` prohibido (usar helpers TZ Ecuador).
- Suite de tests: `python -m pytest tests/ -q` (**351+ tests**: aislamiento entre usuarios, concurrencia, corrupción, anti-duplicado, horarios, identidad, config de tenant, módulos, metering, extracciones de capas, packaging, observabilidad). Correrla antes de cualquier deploy. `test_safe_json` de concurrencia es flaky en Windows (pasa aislado).
- Código retirado vive en `archive/` con justificación en `archive/README.md`. NO restaurar sin leerla. Retirados: `weekly_report.py` (roto y huérfano — reemplazado por `weekly_summaries` del bot), `agent.py`, `apollo_orchestrator.*`, `run_reply_agent.bat`, `run_weekly_report.bat`, `apollo_completion_notifier.py` + `run_apollo_notifier.bat` (retirado 2026-07-03 a pedido de Daniel; su schtask local fue eliminada), `apollo_stats.py` (huérfano).

> **Auditoría 2026-06-22:** ver `AUTOMATIZACIONES_EMPRESA.md` (inventario maestro de
> bots, agentes y automatizaciones + plan de migración). Esa auditoría actualizó los
> pendientes de abajo y agregó la sección "Módulos adicionales" más abajo.

**Pendientes operativos (actualizados 2026-07-04, fases VER-IA):**
1. ✅ GitHub conectado (`mateoalvarado20-maker/biodegradables-platform`), branch protection ACTIVA en master.
2. ✅ `ADMIN_API_TOKEN` propio en el App Service (fail-closed, sin fallback al secret OAuth). Dead-man switch desplegado: webtest `webtest-bot-deadman` → `/health/deliveries` + action group `ag-veria-alertas`.
3. ✅ Cutover de logística al bot (`LOGISTICS_IN_BOT=1`) y de prospección (`REPLY_AGENT_IN_BOT=1`, 2026-07-03) — azfunc quedó SIN timers activos.
4. **F4.3b (~2026-07-10, tras soak del reply agent):** borrar Function App `func-biodegradables-ec`, carpeta `azfunc/`, `tools/sync_azfunc.py` y el gate de drift del CI.
5. **F1 (acción de Daniel):** M365 de VER-IA con el dominio comprado, suscripción Azure propia, GitHub org + transferencia del repo, cuenta Anthropic propia (rotar key al tenerla), acuerdo de IP con Biodegradables/Mateo. La prueba E2E de `ops/provision_tenant.py` (tenant demo de VER-IA) está bloqueada por esto.
6. **F4.5b:** mover el repo fuera del home de Mateo (idealmente junto con la transferencia a la org).
7. (Opcional) `DISPATCH_TABLE_CONN` en la PC para que `dispatch.py` escriba a la tabla de producción; migrar secrets de Biodegradables a Key Vault; rate limiting en `/admin/*`.

**Estado operativo REAL (verificado 2026-07-04):**
| Qué | Dónde corre HOY |
|---|---|
| TODO lo programado | Jobs APScheduler en `teams_bot` (App Service `biodegradables-bot-app`), horarios desde `tenants/biodegradables/config.yaml` (`schedules:`). Con catch-up (`catchup_retry`) + dead-man (`/health/deliveries`). |
| Reporte comercial 8 AM | Job `morning_sales_report` del bot. |
| Reporte logística 8 AM | Job del bot (`LOGISTICS_IN_BOT=1`). Timer azfunc deshabilitado. |
| Reply agent cada 15 min | Job `reply_agent_tick` del bot (`REPLY_AGENT_IN_BOT=1`, auth delegada vía `MSAL_CACHE_B64`, state en Azure Table). Timer azfunc deshabilitado 2026-07-03. |
| Apollo notifier | **RETIRADO 2026-07-03** a pedido de Daniel (archivo en `archive/`). Schtask local eliminada. |
| PC de Mateo | **CERO tareas de producción.** Task Scheduler local ya no ejecuta nada del sistema. |
| azfunc `func-biodegradables-ec` | Sin timers activos. Se borra en F4.3b. |
| Observabilidad | Application Insights `appi-biodegradables-prod` conectado al bot (logs+requests+deps). Verificado 2026-07-04. |

---

## Stack

- Producción: App Service Linux, Python 3.12, FastAPI + gunicorn/uvicorn (**1 worker DELIBERADO**: lease del scheduler + locks son por proceso), APScheduler. También hay `Dockerfile` (build + smoke en CI).
- Dev local: Python 3.14 (Windows Store), usuario `Mateo`.
- Librerías: `anthropic`, `msal`, `httpx`, `botbuilder-core`, `pydantic` v2, `pyyaml`, `azure-data-tables`, `azure-monitor-opentelemetry`.
- Config: pydantic strict (`core/config/schema.py`) sobre `tenants/<slug>/config.yaml`; secrets declarados en `integrations.yaml` (keyvault|env).
- Fuente de datos: Contifico REST (el DAX/Power BI de abajo es LEGACY — el reporte diario ya no lo usa).
- Envío: Microsoft Graph app-only (`graph_mail.py`); delegado MSAL solo para el reply agent (Outlook drafts).

---

## Archivos del proyecto

| Archivo | Función |
|---|---|
| `pbi_cloud.py` | Cliente. Auth MSAL + queries DAX REST + envío Graph mail |
| `pbi_ask.py` | CLI para queries ad-hoc. Lo uso yo (Claude) cuando el usuario me pregunta cosas |
| `pbi_discover.py` | Descubre el esquema del modelo (tablas, columnas, medidas) y lo guarda en `pbi_schema.json` |
| `pbi_diagnose.py` | Verifica qué dataset está vinculado al reporte publicado y muestra el último refresh |
| `pbi_test_dax.py` | Smoke test de queries DAX |
| `daily_report.py` | Genera y envía el correo de apertura del día |
| `run_morning.bat` | Wrapper que Task Scheduler ejecuta a las 8 AM |
| `pbi_schema.json` | Esquema del modelo (cache local, regenerable con `pbi_discover.py`) |
| `hubspot_client.py` | Cliente HubSpot REST API. Trae los 4 KPIs de marketing (leads ayer, top fuente, deals ganados, pipeline) que aparecen en el correo |
| `ask_agent.py` | **Agente conversacional**. Recibe pregunta en lenguaje natural, usa Claude API con tools para consultar PBI + HubSpot, devuelve respuesta. Es el "cerebro" del futuro bot de Teams (Phase 2). Modelo: claude-sonnet-4-6. Uso: `python ask_agent.py "tu pregunta"` |
| `outlook_client.py` | Cliente Microsoft Graph para inbox + drafts. `list_unread_inbox()`, `get_message()`, `create_draft_reply()`. Reutiliza el MSAL cache de `pbi_cloud.py`, scope adicional `Mail.ReadWrite` |
| `apollo_rest.py` | Cliente Apollo.io REST API para enriquecer prospectos. `enrich_by_email()` usa `/people/match`. Cache local en `~/.claude-agent/apollo_cache.json` (TTL 30 días) para no quemar créditos |
| `reply_agent.py` | **Agente de respuestas automáticas a prospectos**. Lee inbox, enriquece remitente vía Apollo, genera borrador personalizado con Claude (sonnet-4-6) y lo crea en Outlook Drafts. Soporta `--dry-run`. State en `~/.claude-agent/reply_state.json` |
| `run_reply_agent.bat` | Wrapper que Task Scheduler ejecuta cada 15 min. Logs en `logs/reply-AAAAMMDD.log` |
| `company_context.md` | Contexto editable de la empresa (catálogo, diferenciadores, reglas de tono) que `reply_agent.py` carga como system prompt. Editable a mano sin tocar código |
| `calendar_client.py` | Cliente Microsoft Graph para Calendario. `create_yearly_all_day_event(user_email, ...)` crea eventos all-day con recurrencia anual y reminder. Si `user_email=None` usa `/me`, si tiene valor usa `/users/{email}` (requiere calendario compartido con permiso Editor). Scopes: `Calendars.ReadWrite` + `Calendars.ReadWrite.Shared` |
| `setup_payment_reminders.py` | Crea recordatorios recurrentes de pagos en calendario de Daniel. Anuales: Patente Municipal 12 may (10d), Super de Bancos 30 sep (10d), CONTIFICO 1 oct (15d). Mensuales: Claude día 18 (3d), Microsoft 365 día 15 (3d). Idempotente — skip si subject ya existe. Flags: `--self`, `--dry-run`, `--replace SUBSTRING` para reemplazar uno existente. Agregar más pagos = editar lista `PAYMENTS` y correr |
| `teams_bot.py` | **EN PRODUCCIÓN — entrypoint de la plataforma.** FastAPI + Bot Framework (dual-bot) + APScheduler con TODOS los jobs programados. Tras los splits F4 quedó como orquestador (~4.100 líneas): cards en `bot_cards.py`, reportes en `team_reports.py`, roles en `tenant_roles.py`, endpoints admin en `admin_api.py` (re-exports de compat). Env vars: `MICROSOFT_APP_*`, `ADMIN_API_TOKEN`, `TENANT_SLUG`, `TENANT_CONFIG_SOURCE=yaml`, flags de cutover. |
| `manifest.json` | Template del manifest de Teams. Tiene 2 placeholders `REEMPLAZAR_CON_MICROSOFT_APP_ID` que se sustituyen cuando se crea el Azure Bot resource. Empaquetar como .zip con `color.png` + `outline.png` y subir a Teams via Apps → Upload custom app. |
| `generate_bot_icons.py` | Genera `color.png` (192x192) + `outline.png` (32x32) con el verde corporativo. Usa Pillow. Ya están generados. |
| `requirements_bot.txt` | Dependencias adicionales solo para teams_bot.py (no necesarias para daily_report.py): fastapi, uvicorn, botbuilder-core, botbuilder-schema, aiohttp, Pillow. |
| `contifico_client.py` | Cliente REST de Contifico POS. `get_documentos(fecha_inicial, fecha_final, tipo="FAC")` con paginación. Auth: header `Authorization: <API_KEY>` (sin Bearer). Lee `CONTIFICO_API_TOKEN`. Base URL `https://api.contifico.com/sistema/api/v1` |
| `daily_logistics_report.py` | **Reporte de logística diario para Gabriela**. Trae facturas en rango `DIAS_DESDE..DIAS_HASTA` (default T-2 a T-1, ambos inclusive), agrupadas por día. Cada día tiene sub-secciones "Envíos desde Guayaquil" y "Envíos desde Quito", cada una con sub-tablas "Dentro de la ciudad" y "A otras provincias". Cierra con tabla unificada por provincia. Columna "Estado" lee `dispatch_state.json` (badges OK/NO/PARCIAL). Modos: `morning` (solo a gsanchez@, sin CC), `test` (solo Mateo), `dry` (stdout). Origen Quito vs Guayaquil deducido del prefijo del documento (`001-001`=GYE, `001-002`=UIO). Provincia/ciudad destino parseadas por keywords del campo `persona.direccion`. La sección "Pendientes de días anteriores" está temporalmente comentada (se volverá a habilitar después como "pendientes de entregar") |
| `run_logistics.bat` | Wrapper Task Scheduler 8 AM diario. Logs en `logs/logistics-AAAAMMDD.log` |
| `dispatch_state.py` | Persistencia del estado de despacho. State file: `~/.claude-agent/dispatch_state.json`. Funciones: `mark(factura, status, razon, marcado_por)`, `get(factura)`, `is_ok(factura)`, `load()`, `save()`. Compartido por CLI Fase 1 (`dispatch.py`) y bot Fase 2 (Teams) |
| `dispatch.py` | CLI para marcar manualmente despachos. Subcomandos: `mark FACTURA OK\|NO\|PARCIAL [--razon "..."] [--por "jefe_uio"]`, `status`, `list-pending`, `clear FACTURA`. Forza UTF-8 en stdout |
| `apollo_orchestrator.py` | **[DESHABILITADO 2026-05-28]** Era el orquestador "1 sola secuencia activa". El user lo descartó porque limitaba volumen — perdía dinero al apagar 10/11 secuencias. Archivos preservados por si se reaprovecha la lógica en otro modo. Tarea programada eliminada. No usar. |
| `apollo_orchestrator.json` | Config del orquestador deshabilitado. Conservada por si se reusa. |
| `run_apollo_orchestrator.bat` | Wrapper del orquestador deshabilitado. Conservado. |
| `apollo_completion_notifier.py` | **RETIRADO 2026-07-03** a pedido de Daniel ("no lo necesito"). Vive en `archive/` con su wrapper; schtask local eliminada. Reversible: ver `archive/README.md`. |
| `activities_template.json` | Plantilla de actividades recurrentes que Mateo debe ejecutar cada semana (Apollo 70 correos/día, TikTok video+live, códigos Contifico, chatbots, etc.). Se lee SOLO al inicializar una semana nueva en `activity_state.py`. Editable a mano para agregar/quitar/ajustar metas. |
| `activity_state.py` | Persistencia del tracker de actividades semanales. State en `~/.claude-agent/activity_state.json`, una entry por semana ISO (`AAAA-Www`). Funciones: `init_week`, `mark_daily`, `set_weekly_progress`, `add_adhoc`, `remove_activity`, `daily_total`, `daily_compliance`. |
| `activity_tracker.py` | CLI para tracking de actividades (estilo `dispatch.py`). Subcomandos: `done` (valor diario), `progress` (% avance semanal), `add` (ad-hoc), `remove`, `status`/`week`. Para uso de Mateo durante la semana mientras se implementa el bot de Teams en Fase 2. |
| `weekly_report.py` | **ARCHIVADO** (roto y huérfano). Reemplazado por el job `weekly_summaries` del bot. NO crear su schtask. |
| `azure_setup_checklist.md` | Runbook paso a paso para Daniel/admin: provisionar Azure Bot + App Service + permisos Graph + manifest Teams. Pre-requisito para Fase 2 del tracker (slash commands en Teams) y para que `teams_bot.py` salga a producción. |
| `graph_mail.py` | Cliente Microsoft Graph para enviar correo via Service Principal (client_credentials con APP_ID + SECRET del bot). NO usa MSAL cache — funciona desde Azure App Service. Función `send(from_user, to, subject, html_body, cc)`. Token cacheado en memoria ~50 min. |
| `activities_manifest.json` | Manifest del Activities Bot (botId `bc908e6c-a2a0-4252-9760-2d3c5f17a3f6`). Empaquetado con icons en `activities_teams_app.zip` para sideload Teams. |
| `reminders.py` | Persistencia de recordatorios programados (Phase E + G.1). State en `~/.claude-agent/reminders.json`. Soporta recurrencia (daily, weekly, weekdays, monthly, weekly_<day>). Daniel/Gabriela los crean via Data Bot tool `schedule_reminder_for_collaborator`. El scheduler del bot (`deliver_due_reminders`, cada 5 min) los entrega via activities_adapter al colaborador, y si tienen recurrence los reprograma automáticamente. |
| `activities_template_gsanchez.json` | Template de Gabriela (vacío inicial — agrega ad-hoc por chat). |
| `activities_template_info.json` | Template del colaborador GYE (info@). Pre-cargado con `cobranzas-gye`. |
| `activities_template_quito.json` | Template del colaborador UIO (quito@). Pre-cargado con `cobranzas-uio`. |

### Módulos adicionales (documentados en auditoría 2026-06-22)

Estos estaban en producción pero faltaban en la tabla de arriba:

| Archivo | Función | Estado |
|---|---|---|
| `monthly_recap.py` | Genera 2 correos mensuales (recap de ventas + recap de actividades) a gerencia el día 1 (jobs `monthly_*_recap_day1`). Usa Contifico + `forecasting` + `news_brief` + `graph_mail`. CLI: `send-sales`/`send-activities [YYYY MM]` | ✅ Activo |
| `news_brief.py` | Brief diario de noticias (economía EC, supply chain, sector empaques) con Claude `sonnet-4-6` + web_search nativo. Job `daily_news_brief` 6 AM. Se inyecta al system prompt del Data Bot. Escribe `~/.claude-agent/daily_news_brief.json` | ✅ Activo |
| `forecasting.py` | Proyecciones de ventas (pesimista/probable/optimista) sobre histórico Contifico (same-month-last-year × YoY ±15%). Sin IA. Lo usan `monthly_recap` y `ask_agent` (`forecast_sales_for_month`, `analyze_product_mix`) | ✅ Activo |
| `conversation_history.py` | Persistencia multi-turn del chat por usuario (TTL 30 min, separado por bot). Usado por `teams_bot`. State `~/.claude-agent/conversation_history.json` (safe_json) | ✅ Activo |
| `graph_calendar_app.py` | Cliente Graph **app-only** para calendario: crea/actualiza/borra eventos de fecha límite y reuniones en calendarios de gerencia. Usado por `teams_bot` y `ask_agent`. Requiere admin consent `Calendars.ReadWrite` | ✅ Activo |
| `credito_excel.py` | Lee condiciones de crédito (días por cliente) desde Excel en SharePoint (Graph workbook). Fallback a `condiciones_credito.json`. Vive sincronizado en `azfunc/` | ✅ Activo (azfunc) |
| `apollo_stats.py` | Métricas de prospección Apollo | 🗄️ ARCHIVADO 2026-07-03 (huérfano) |
| `wp_client.py` | Cliente REST de WordPress (Basic Auth + Application Password). Solo lectura habilitada. Base de los `wp_*` | ✅ Activo |
| `wp_audit.py` / `wp_check.py` / `wp_drafts.py` | Auditoría/smoke-test/inspección del WordPress. CLIs manuales, no automatizados | 🔧 Manual |
| `wp_apply.py` | Aplicación controlada de cambios al WordPress. Dry-run por defecto; exige `--apply --approve <id>`; guarda backups | 🔧 Manual |
| `graph_mail.py` | Envío de correo vía Service Principal (app-only, client_credentials). NO usa MSAL. Usado por todos los reportes del bot/azfunc | ✅ Activo |
| `core_config.py` | Config de negocio (destinatarios, feriados, `META_FACTOR`, `PY_OVERRIDE`, umbrales, horarios, módulos). **En producción ya NO es la fuente de verdad**: `_maybe_load_from_tenant()` sobreescribe todo desde el YAML del tenant. Los defaults hardcodeados solo sirven a `azfunc/` (legacy, muere en F4.3b) y a runs locales sin `TENANT_CONFIG_SOURCE` | ✅ Activo |
| `safe_json.py` / `send_ledger.py` | Infraestructura: escritura atómica + backup + cuarentena + locks; ledger anti-duplicado de envíos | ✅ Activo |

### Módulos de la plataforma VER-IA (2026-07)

Nacidos en las fases F0–F5 del pivot (memoria `project_veria_pivot`):

| Archivo | Función | Estado |
|---|---|---|
| `tenants/<slug>/config.yaml` | **Fuente de verdad del tenant**: company, people/roles, schedules de todos los jobs, timezone, modules, metas, feriados, caja, prefijos ERP, branding. `tenants/_template/` documentado para clientes nuevos | ✅ Prod (`biodegradables`) |
| `tenants/<slug>/integrations.yaml` | Declaración de secrets por integración (erp/crm/mail/ai/prospecting/wordpress), cada uno con fuente exactamente-una: `keyvault:` o `env:`. Nunca valores | ✅ Prod |
| `core/config/schema.py` | Schema pydantic v2 strict (`extra="forbid"`) del config.yaml. `KNOWN_MODULES` espejo de `MODULES` (test fija igualdad) | ✅ |
| `core/config/integrations.py` | `SecretRef` + `load_tenant_integrations()` | ✅ |
| `bot_cards.py` | Todos los builders de Adaptive Cards (check-in, José/ruta, cierre de caja…) | ✅ Prod |
| `team_reports.py` | Reportes de equipo (consolidado 18:30, weekly summaries, cierre caja email…) + `_load_collaborators()` | ✅ Prod |
| `tenant_roles.py` | Roles/emails especiales del tenant (INFO/QUITO/JOSE, `SUPERVISORS_ONLY`, `CIERRE_CAJA_USERS`, sucursales…) — capa entre core_config y el resto | ✅ Prod |
| `admin_api.py` | Los ~39 endpoints `/admin/*` (router FastAPI montado por teams_bot al final). Auth `X-Admin-Token: $ADMIN_API_TOKEN` (fail-closed) | ✅ Prod |
| `llm_usage.py` | **Metering de IA (F3)**: registra cada llamada a Claude por (tenant, agente, modelo) con costo al centavo (`PRICES_USD_PER_MTOK`, cache write/read). `record()` jamás lanza. Presupuesto `LLM_BUDGET_MONTHLY_USD` + job `llm_budget_check` 7:05. CLI: `python llm_usage.py status` | ✅ Prod |
| `ops/provision_tenant.py` | **Aprovisionamiento de cliente nuevo (F5)**: App Registrations + Azure Bots + App Service + Key Vault (`kv-<slug>-veria`, managed identity, referencias `@Microsoft.KeyVault`). Dry-run por defecto; `--expected-tenant-id` como guardia | ✅ Código listo, E2E bloqueado por F1 |
| `ops/gen_teams_app.py` | Genera manifests + iconos (brand_color del YAML) + zips de los 2 bots de un tenant | ✅ |
| `ops/validate_tenant.py` | Valida config.yaml + integrations.yaml de uno o todos los tenants. Corre en CI | ✅ CI |
| `tools/check_core_purity.py` / `tools/check_async_hygiene.py` | Gates CI: pureza de capas y prohibición de llamadas bloqueantes en handlers async | ✅ CI |
| `Dockerfile` + `.dockerignore` | Imagen python:3.12-slim, gunicorn+uvicorn 1 worker. Build + smoke de imports en CI | ✅ CI |
| `demo_contifico.py` / `demo_hubspot.py` / `demo_seed.py` | `DEMO_MODE=1`: datos ficticios (empresa Andex) para demos comerciales sin tocar datos reales | ✅ |

## Phase E+F+G: Gestión de equipo, cobranzas, recurrencias (2026-05-30 / 31)

**Phase E — Gerencia asigna a colaboradores:** Daniel + Gabriela usan el Data Bot para asignar actividades (`add_activity_for_collaborator`) y programar recordatorios (`schedule_reminder_for_collaborator`) a otros colaboradores. Tools en ask_agent modo `data`. `KNOWN_COLLABORATORS` env var con alias→email: `mateo`, `gabriela`, `info`/`gye`, `quito`/`uio`.

**Phase F — Cobranzas auto-asignadas:** Scheduler Lun-Vie 7:30 AM EC (`auto_assign_cobranzas`). Pull cartera vencida por ciudad via `contifico_client.cartera_vencida_por_ciudad(UIO|GYE, 5)`. Top 5 por ciudad se auto-crean como ad-hoc activities en el state del colaborador correspondiente (UIO→quito@, GYE→info@). Aparecen en el check-in card de las 16:30. Cada colaborador marca contactado/intentado/no hecho con justificación. El cierre del día envía resumen a Daniel + Gabriela.

**Phase G.1 — Reminders recurrentes:** El parámetro `recurrence` permite (`daily`, `weekly`, `weekdays`, `monthly`, `weekly_mon`..`weekly_sun`). El bot re-crea el siguiente reminder después de entregarlo.

**Phase G.2 — Weekly summaries automáticos:** Scheduler Viernes 17:00 EC (`send_weekly_summaries`). Por cada colaborador con state, genera HTML con tabla diaria por día + actividades semanales + cobranzas + comparativo vs semana anterior, envía al supervisor (env var `TRACKER_EMAIL_TO_<ALIAS>`).

**Env vars del cluster Phase E-G (en Azure App Service):**
- `KNOWN_COLLABORATORS=mateo:malvarado@,gabriela:gsanchez@,info:info@,quito:quito@,gye:info@,uio:quito@` (todos `@biodegradablesecuador.com`)
- `TRACKER_EMAIL_TO_MATEO=dsanchez@,gsanchez@`
- `TRACKER_EMAIL_TO_GABRIELA=dsanchez@`
- `TRACKER_EMAIL_TO_INFO=dsanchez@,gsanchez@`
- `TRACKER_EMAIL_TO_QUITO=dsanchez@,gsanchez@` (mismo para `_GYE` y `_UIO` aliases)

**Admin endpoints para testing (require `X-Admin-Token: <MICROSOFT_APP_PASSWORD>`):**
- `POST /admin/trigger-checkin` — dispara check-in card
- `POST /admin/trigger-reminders` — entrega reminders vencidos
- `POST /admin/trigger-cobranzas` — corre auto-asignación cobranza
- `POST /admin/trigger-weekly-summaries` — manda weekly summaries

**Scheduler jobs activos en producción:**
- `checkin_weekday` — Lun-Vie 16:30 EC
- `checkin_saturday` — Sáb 12:30 EC
- `deliver_reminders` — cada 5 min
- `auto_assign_cobranzas` — Lun-Vie 7:30 EC
- `weekly_summaries` — Vie 17:00 EC
- `consolidated_daily_summary` — Lun-Vie 18:30 EC (Phase O)

## Phase N + O (2026-06-02)

**Phase N — Cierre de caja diario para info@/quito@:** sub-card en el Adaptive Card del check-in con 12 `Input.Number` (denominaciones USD: $100/$50/$20/$10/$5/$1 papel + $1 moneda/50¢/25¢/10¢/5¢/1¢) + un Input.Text chiquito para notas. Solo se agrega cuando `user_email in CIERRE_CAJA_USERS = {info@, quito@}`. El bot suma todo, resta `CAJA_FONDO_FIJO=$50` (siempre queda $50 en caja), guarda en `activity_state.cierres_caja[fecha]` y manda correo separado a Daniel + Gabriela Sánchez (CC Mateo) via `_send_cierre_caja_email`. Sucursal se deduce del email (info=Guayaquil, quito=Quito).

**Phase O — Consolidated daily summary:** Reemplaza los emails individuales por colaborador. Cuando alguien hace check-in, el bot solo confirma en chat ("📧 El resumen consolidado llega a 6:30 PM"). A las **18:30 EC Lun-Vie** un job (`send_consolidated_daily_summary_job`) manda UN solo correo "Resumen diario del equipo — DD/MM/YYYY" a `dsanchez@,gsanchez@` (CC Mateo) con un bloque por colaborador (header con horario + summary line + tabla daily activities + proyectos avanzados + proyectos pendientes + banner cierre de caja si aplica). El cierre de caja sigue mandándose aparte (info financiera urgente). Override via env vars `CONSOLIDATED_DAILY_TO` / `CONSOLIDATED_DAILY_CC`.

**Phase O — Supervisors filtering:** `SUPERVISORS_ONLY = {dsanchez@}` en `teams_bot.py`. Daniel NO trackea actividades propias — su rol es revisar las de los demás. `send_daily_checkin` lo excluye automáticamente, y los emails consolidados/cierre nunca pasan por su slot de colaborador.

**Phase O — Quincenal DESHABILITADO:** El scheduler job `midmonth_sales_status_day15` (resumen quincenal día 15) se quitó. Mateo lo descartó: "no me gustó el quincenal, no es necesario". El endpoint `/admin/trigger-midmonth-status` queda disponible para disparo manual si algún día se necesita.

**Admin endpoints nuevos (require `X-Admin-Token: <MICROSOFT_APP_PASSWORD>`):**
- `POST /admin/seed-template-for-user` — body `{user_email}`. Sincroniza el template del user con su semana actual (idempotente: solo agrega lo que falta, no borra ni duplica). Útil cuando se edita el template y se quiere que aparezca YA sin esperar al lunes.
- `POST /admin/set-priorities-for-user` — body `{user_email, priorities: {aid: "alta|media|baja"}}`. Marca prioridades en batch.
- `POST /admin/remove-activity-for-user` — body `{user_email, activity_id}`. Borra una activity puntual de la semana actual.
- `GET /admin/show-activities-for-user?user_email=...` — devuelve las activities del user (o de todos si no se pasa). Para debug.
- `POST /admin/wipe-user-from-activities` — body `{user_email}`. Borra TODO el state del user + su ref del Activities Bot (NO toca Data Bot).
- `POST /admin/trigger-consolidated-daily-summary` — dispara el consolidado ahora. Body opcional `{to_override, cc_override}` para previews.

**Estado actual de los colaboradores (2026-06-02):**
- `malvarado@` (Mateo): 7 activities del template default (Apollo correos/respuestas, chatbots, Códigos Contifico, WordPress audit, video-tiktok)
- `gsanchez@` (Gabriela Sánchez): 7 activities ad-hoc (scrum diaria, pagos quincena, pedidos inicio mes, seguimiento personalizados, seguimiento exportación, revisión reportes fin de mes, depósito efectivo) — prioridades marcadas (alta/media/baja)
- `info@` (Gabriela Bravo): 2 activities (cierre-caja-semanal ad-hoc + cobranzas-gye del template) + SUB-CARD cierre de caja al final del check-in (Phase N)
- `dsanchez@` (Daniel): vacío — está en `SUPERVISORS_ONLY`

**Templates `activities_template_*.json` reales:**
- `activities_template.json` — default (Mateo): Apollo, chatbots, Códigos Contifico, WordPress, video-tiktok
- `activities_template_gsanchez.json` — Gabriela Sánchez: 7 actividades (las que enumeran arriba)
- `activities_template_info.json` — info@: cierre-caja-gye
- `activities_template_quito.json` — quito@: cierre-caja-uio

## Arquitectura dual-bot en Teams (Phase D — 2026-05-30)

Dos bots distintos sirviendo audiencias diferentes, ambos hosteados en el mismo App Service (`biodegradables-bot-app`):

| | **Data Bot** | **Activities Bot** |
|---|---|---|
| App ID | `8ef9d83a-914e-47de-9850-f630b172fc8f` | `bc908e6c-a2a0-4252-9760-2d3c5f17a3f6` |
| Azure Bot resource | `biodegradables-data-bot` | `biodegradables-activities-bot` |
| Endpoint | `/api/messages` | `/api/activities/messages` |
| Acceso | Daniel, Gabriela, Mateo (`BOT_ALLOWED_USERS_DATA`) | Cualquier colaborador del tenant |
| Tools (ask_agent mode) | `data`: Contifico + HubSpot | `activities`: tracker + email |
| Adaptive Card | No | Sí (check-in diario) |
| Scheduler | Ninguno | APScheduler Lun-Vie 16:30 + Sáb 12:30 |
| Proactive email | No | Sí, al completar check-in → `TRACKER_EMAIL_TO` |
| State per-user | N/A | Sí (`activity_state.json` con estructura `users/<email>/weeks/...`) |

---

## Constantes clave del reporte comercial

> **NOTA 2026-07:** `daily_report.py` ya NO usa Power BI/DAX — lee Contifico
> directo (migración 2026-06-10) y envía con `graph_mail` (app-only). Los
> valores de abajo (meta, PY_OVERRIDE, feriados, umbrales) hoy vienen del
> YAML del tenant vía `core_config`. La mecánica del cálculo sigue vigente.

| Constante | Valor | Para qué |
|---|---|---|
| `DATASET_ID` | `5b04e54f-4c15-4c67-9fcf-a0aad424a17f` | El dataset Contifico real (no el "Copia") |
| `REPORT_URL` | `https://app.powerbi.com/groups/me/reports/de5387d4-...` | Link al dashboard que se incluye en el footer |
| `JEFE` | `["dsanchez@...", "gsanchez@..."]` | Destinatarios principales |
| `MIO` | `malvarado@...` | Copia (cc) y modo test |
| `PY_OVERRIDE` | `{5: 38000.0}` | Override de "ventas mismo mes año anterior" cuando PBI no coincide con Contifico |
| `EC_HOLIDAYS` | dict por año | Feriados de Ecuador para calcular días hábiles |
| Umbrales semáforo | `CUMPL_VERDE=1.00, CUMPL_AMARILLO=0.85`, etc. | Verde/amarillo/rojo |

---

## Cómo funciona el cálculo de la meta

1. Power BI devuelve `[Ventas Reales]` filtrado al mismo mes del año anterior → `_VentasMesLY`
2. Si `PY_OVERRIDE[mes]` existe, sustituye ese valor (porque PBI a veces difiere de Contifico)
3. Meta del mes = `PY × 1.20` (crecimiento 20%)
4. Meta diaria base = `Meta / días_hábiles_del_mes` (excluye domingos y feriados Ecuador)
5. Cumplimiento de ayer = `ventas_ayer / meta_diaria_base`
6. Cumplimiento vs día = `MTD / (meta_diaria_base × días_hábiles_transcurridos)`

---

## Esquema del modelo Power BI (Contifico) — LEGACY

> Solo relevante para consultas ad-hoc con `pbi_ask.py`. Ningún reporte
> automatizado depende ya de Power BI.

**Tablas:** Ventas, Calendario, Inventario, Bodega, Cobranzas, DimClientes,
ClientesConCredito, ConfiguracionCredito.

**Medidas clave:**
- Ventas: `Ventas Reales`, `Ventas MTD`, `Ventas Día`, `Meta Mensual`,
  `Cumplimiento %`, `Brecha Meta`, `Ritmo Diario Necesario`,
  `Ticket Promedio`, `Clientes Únicos`
- Cobranzas: `Cartera Total`, `Cartera Vencida`, `Cartera No Vencida`,
  `% Cartera Vencida`, `Deuda Vencida por Cliente`,
  `Cartera 1-30 Días`, `Cartera 31-60 Días`, `Cartera +90 Días`,
  `Efectividad Cobranza %`, `Dias Promedio Atraso`

**Columnas útiles:**
- `Ventas[Fecha]`, `Ventas[Ciudad]` (UIO/GYE), `Ventas[Vendedor]`,
  `Ventas[Total]`, `Ventas[Cliente]`
- `Cobranzas[persona.razon_social]`, `Cobranzas[vendedor.direccion]`
  (UIO/GYE), `Cobranzas[Antiguedad Cartera]`, `Cobranzas[saldo]`

**Detalle completo del esquema:** ver `pbi_schema.json`.

**OJO:** Las medidas `[Ventas MTD]` y `[Ventas Día]` devuelven BLANK si se
llaman sin contexto de fecha. Siempre envolverlas en `CALCULATE` con filtros
explícitos del Calendario.

**Funciones DAX disponibles en este nivel de licencia:**
- ✅ `INFO.VIEW.TABLES()`, `INFO.VIEW.MEASURES()`, `INFO.VIEW.COLUMNS()`
- ❌ `INFO.TABLES()`, `INFO.MEASURES()`, `INFO.COLUMNS()` (las versiones sin VIEW fallan)

---

## Tareas programadas

**Desde 2026-07-03 NO existe ninguna tarea de producción en el Task Scheduler
de esta PC ni ningún timer activo en Azure Functions.** Todo lo programado
corre como jobs APScheduler dentro de `teams_bot` (App Service), con horarios
definidos en `tenants/biodegradables/config.yaml` → `schedules:`. NO crear
schtasks locales — duplicarían envíos.

Jobs activos (nombres = claves de `JOB_SCHEDULES`): `morning_sales_report`,
`logistics_morning`, `reply_agent_tick` (15 min), `daily_news_brief`,
`auto_assign_cobranzas`, `checkin_weekday`/`checkin_saturday`,
`deliver_reminders` (5 min), `weekly_summaries`,
`consolidated_daily_summary`, `monthly_*_recap_day1`, `llm_budget_check`,
`catchup_retry`, `jose_asistencia`, `apertura_caja_matinal`.

Operación:
```powershell
# Salud del bot y de las entregas del día (dead-man)
curl https://biodegradables-bot-app-cvgnasgec8eqatdg.centralus-01.azurewebsites.net/health
curl .../health/deliveries          # 200 = todo entregado según schedule, 503 = falta algo

# Disparo manual de un job (auth X-Admin-Token: $env:ADMIN_API_TOKEN)
# ver endpoints /admin/trigger-* en admin_api.py

# Runs manuales locales (nunca programados):
python daily_report.py test
python daily_logistics_report.py dry
python reply_agent.py --dry-run --since-hours 24 --verbose
```

**LECCIÓN APRENDIDA (histórica):** las tareas llegaron a vivir en 3 lugares
(schtasks locales, timers azfunc, jobs del bot). Antes de declarar algo
"desactivado" o "único", chequear TODOS. Hoy el único lugar es el bot;
`azfunc/` queda sin timers y se elimina en F4.3b.

---

## Cómo responder preguntas sobre Power BI — LEGACY

> Para preguntas de datos, hoy lo normal es `contifico_client.py` /
> `ask_agent.py`. Esto queda para cuando el usuario pida algo del dashboard PBI.

Cuando el usuario te pregunte algo sobre datos de PBI (ej. "cuánto vendimos hoy",
"cuál es el top deudor de Quito"), tu flujo es:

1. Mira `pbi_schema.json` para identificar la medida o columna relevante.
2. Construye una query DAX. **Siempre con `EVALUATE`** y, si usas medidas con
   contexto de fecha, envuelve en `CALCULATE` con filtros de `'Calendario'[Anio]`,
   `[Mes]`, `[Date]`.
3. Escribe la query a un archivo temporal y ejecuta:
   ```powershell
   Set-Content -Path query.dax -Value $dax -Encoding utf8
   python pbi_ask.py --dax `@query.dax
   ```
   (El `--dax @archivo` evita problemas de escapado de comillas).
4. Limpia el archivo: `Remove-Item query.dax`.
5. Interpreta el resultado y responde en lenguaje natural.

**Ejemplo de DAX típica para "cuánto vendimos hoy":**
```dax
EVALUATE ROW(
    "VentasHoy",
    CALCULATE([Ventas Reales], 'Calendario'[Date] = TODAY())
)
```

**Otra forma rápida** (sin archivo temporal, para queries simples):
```powershell
python pbi_ask.py --refresh-status   # cuándo fue el último refresh
python pbi_ask.py --measures          # listar medidas disponibles
python pbi_ask.py --schema            # esquema completo
```

---

## Reply agent: cómo funciona

`reply_agent.py` es el sistema de respuestas automáticas a prospectos.
**Desde 2026-07-03 corre como job `reply_agent_tick` del bot** (cada 15 min,
`REPLY_AGENT_IN_BOT=1`): auth delegada vía `MSAL_CACHE_B64` (cache MSAL
serializado en app setting — se mantiene vivo con el uso; si expira, runbook
§MSAL_CACHE_B64), state en Azure Table, textos de empresa/firma desde
`core_config` (COMPANY_DOMAIN, OUTBOUND_SIGNER). Flujo:

1. Lee correos no leídos del inbox de Mateo (Graph API, scope `Mail.ReadWrite`)
2. Filtra: descarta correos del dominio propio, no-reply, auto-respuestas
3. Para cada candidato, llama `apollo_rest.enrich_by_email(sender)`:
   - Si Apollo no lo encuentra → no es prospecto, skip
   - Si lo encuentra → continúa con datos del contacto + empresa
4. Trae el correo completo con body (que incluye thread citado abajo)
5. Llama Claude `sonnet-4-6` con el `company_context.md` como system prompt
6. Claude devuelve JSON `{should_draft, body_html, reason_if_skip}`
7. Si `should_draft=true`, crea borrador vía `/createReply` (Graph). Aparece
   enhebrado en la carpeta Drafts de Outlook
8. Persiste el `message_id` para no duplicar borradores (Azure Table en
   producción; `~/.claude-agent/reply_state.json` en runs locales)

**Cómo ajustar el comportamiento sin tocar código:**
Edita `company_context.md`. Cambios típicos:
- Activar/desactivar mención de Gabriela (ver la sección "Contacto comercial")
- Agregar producto nuevo a una categoría
- Cambiar el tono de la plantilla de cierre
- Agregar nuevo tipo de industria al matching table
- Agregar/quitar reglas de "No debe hacer"

Después de editar el archivo, el siguiente run del agente lo recoge sin
restart (se carga en cada llamada).

**Cómo limpiar el state (para reprocesar correos ya procesados):**
```powershell
Remove-Item $env:USERPROFILE\.claude-agent\reply_state.json -Force
```

**Cómo limpiar el cache de Apollo (para forzar re-enrichment):**
```powershell
Remove-Item $env:USERPROFILE\.claude-agent\apollo_cache.json -Force
```

---

## Variables de entorno requeridas

Están persistidas a nivel de usuario (`User` scope) en Windows:

| Variable | Valor |
|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` (de console.anthropic.com) |
| `GRAPH_CLIENT_ID` | `8b85d6bf-a34c-4482-821c-ab7a70717776` (app registration en Azure) |
| `GRAPH_TENANT_ID` | `aec07a63-9c6c-4bc1-af6f-edb9aa826d0b` (tenant Biodegradables Ecuador) |
| `HUBSPOT_TOKEN` | `pat-na1-...` (Private App de HubSpot, scopes: contacts.read + deals.read) |
| `APOLLO_API_KEY` | Apollo.io REST API key (scopes: contacts/search, people/match, organizations/enrich, mixed_people/api_search). Genera en https://developer.apollo.io/keys |
| `CONTIFICO_API_TOKEN` | **API_KEY a nivel de empresa** (NO el POS token). Se solicita a soporte Contifico por ticket — distinta del UUID del POS que aparece en el panel. Se envía en header `Authorization: <token>` (sin "Bearer"). Si responde `401 "Empresa matching query does not exist"` es que estás usando el POS token por error. Usada por `contifico_client.py` y `daily_logistics_report.py` |

Para verlas:
```powershell
[System.Environment]::GetEnvironmentVariable('ANTHROPIC_API_KEY', 'User').Substring(0,15) + "..."
```

---

## App registration en Azure AD

- **App name:** `claude-agent`
- **Client ID:** `8b85d6bf-a34c-4482-821c-ab7a70717776`
- **Tenant:** Biodegradables Ecuador
- **Permisos delegated otorgados con admin consent:**
  - `Microsoft Graph` → `Mail.Send`, `Mail.ReadWrite`, `User.Read`, `Calendars.ReadWrite`, `Calendars.ReadWrite.Shared`
  - `Power BI Service` → `Dataset.Read.All`
- **Allow public client flows:** Yes (necesario para device-code flow)

---

## MSAL token cache

- Ruta: `C:\Users\Mateo\.claude-agent\msal_cache.bin`
- Persiste tokens entre ejecuciones para que Task Scheduler corra sin re-auth.
- Refresh tokens duran 90 días — si se vence, el usuario tiene que correr
  `python pbi_cloud.py` interactivo y hacer device-code una vez más.

---

## Conectores MCP disponibles (en este Claude Code)

- **Power BI Modeling MCP** (`mcp__powerbi-modeling-mcp__*`): conecta a Power BI
  Desktop local (no a cloud). Útil si el usuario abre Desktop para inspeccionar
  el modelo. **No usar para queries de datos** — para eso siempre `pbi_ask.py`.
- **HubSpot MCP** (`mcp__f7e6d575-c292-4f10-8dc4-19848df7e177__*`): consulta
  HubSpot del usuario directamente. Útil para agregar KPIs de marketing al
  reporte diario o responder preguntas sobre leads/campañas.
- **Microsoft 365 / Outlook MCP** (`mcp__9fdf05b0-...__*`): búsqueda de correos,
  calendario, SharePoint del usuario.
- **Apollo MCP** (`mcp__a4b83cc2-...__*`): CRM Apollo.
- **SEMrush MCP** (`mcp__44a089f5-...__*`): SEO/keywords research.

---

## Issues conocidos / decisiones de diseño

1. **Power BI Service refresca solo 4×/día** (config user). Si Contifico
   registra ventas después del último refresh, el correo de la mañana no las
   ve hasta el próximo refresh. Para resolver: aumentar frecuencia en PBI
   Service Settings, o agregar `Dataset.ReadWrite.All` y disparar refresh
   manual antes del envío (no implementado todavía).

2. **`fmt_pct` heurística**: si el valor está entre -10 y 10, asume ratio
   decimal y multiplica por 100. Si es mayor, asume que ya viene como porcentaje.
   No es perfecto pero cubre todos los casos del modelo Contifico actual.

3. **Outlook estripa CSS de `<style>`**. Por eso `_kpi` devuelve `<td>` con
   estilos inline y `bgcolor` attribute. No volver a poner el CSS class-based.

4. **Update mensual del PY_OVERRIDE**: cada mes el usuario debe verificar si
   el valor que devuelve PBI para "mismo mes año anterior" coincide con lo
   que ve en Contifico. Si no, agregar al `PY_OVERRIDE` dict.

5. **Spend de Meta Ads + Google Ads NO está conectado todavía**. El correo
   de HubSpot que recibe Daniel semanalmente trae esa data, pero la HubSpot
   API estándar no la expone sin Marketing Hub Pro. Próxima iteración:
   conectar Meta Ads API + Google Ads API para automatizar también el spend.

6. **HubSpot Private App scopes mínimos**: `crm.objects.contacts.read` +
   `crm.objects.deals.read`. Si en el futuro se quiere agregar más KPIs
   (companies, tickets, etc.), hay que agregar el scope en el panel de
   HubSpot y rotar el token.

7. **Apollo plan Basic no expone `emailer_campaigns/search`**. Diseño
   original era "lista campañas activas → procesa solo correos de contactos
   en esas campañas". En plan B usamos `/people/match` por email: si Apollo
   lo encuentra en su base, lo tratamos como prospecto. Si no, skip. Funciona
   bien pero podría procesar correos de no-prospectos (filtrado adicional
   en el agente lo descarta).

8. **Graph filter por `conversationId` falla con "InefficientFilter"**. Por eso
   `reply_agent.py` no trae el thread completo via API — usa el body del último
   correo recibido (que ya incluye el thread anterior citado abajo cuando es
   una respuesta de "Re:"). Suficiente para que Claude entienda el contexto.

9. **Mención de Gabriela Sánchez está DESACTIVADA en `company_context.md`**.
   El user prefiere manejar los primeros correos personalmente. Para activar:
   editar la sección "Contacto comercial" del archivo y la regla en "Sí debe
   hacer". El agente lo recoge en la siguiente corrida sin restart.

10. **Variables de entorno en Task Scheduler**: las tareas heredan env vars
    User-scope al arrancar el proceso, así que `run_reply_agent.bat` no
    necesita exportar nada. Pero cuando se prueba manualmente desde una
    PowerShell que fue abierta antes de setear una env var, hay que
    propagarla explícitamente: `$env:APOLLO_API_KEY = [Environment]::GetEnvironmentVariable('APOLLO_API_KEY', 'User')`.

11. **Contifico no expone provincia ni dirección de despacho separada**. Solo
    `persona.direccion` (string libre del cliente). `daily_logistics_report.py`
    parsea provincia por keywords (ver `PROVINCIA_KEYWORDS`). Si llega una
    dirección que no matchea ningún keyword (ej. solo coordenadas o referencia
    rara), cae en "Sin identificar". Cuando aparezca una provincia o ciudad no
    cubierta, agregarla al dict. Para envíos intra-ciudad (B.E.) se ignora el
    parse y se asigna Pichincha/Guayas según la sucursal de origen (prefijo del
    `documento`).

12. **Sistema de confirmación de despacho (logística) en 2 fases**:
    - **Fase 1 (en uso)**: `dispatch.py` + `dispatch_state.py` para marcar
      manualmente. Mateo/Gabriela usan el CLI. El reporte de la mañana ya lee
      el state y pinta de rojo los pedidos no despachados de los últimos 6
      días.
    - **Fase 2 (martes 2026-05-26)**: Azure Bot se provisiona, se extiende
      `teams_bot.py` para enviar Adaptive Cards a `quito@biodegradablesecuador.com`
      y `guayaquil@biodegradablesecuador.com` (este último se crea ese día). Los
      jefes de bodega marcan desde Teams; el bot escribe al mismo
      `dispatch_state.json`.
    - Los primeros días en producción el reporte mostrará muchos "pendientes"
      por la falta de marcado histórico. Esto se va limpiando con el uso.

13. **Apollo orchestrator requiere Master API Key (RESUELTO 2026-05-26)**.
    Los endpoints `/emailer_campaigns/search`, `/approve` y `/abort` devuelven
    403 con una API key normal: `"is not accessible with this api_key"`. La
    solución fue generar una nueva key en Apollo Settings → Integrations →
    API → "Crear clave API" con el toggle **"Establecer como clave maestra"
    ACTIVADO**. En plan Básico ese toggle SÍ está disponible — los scopes de
    `emailer_campaigns` no aparecen como checkboxes individuales en Basic,
    solo se desbloquean vía Master. La master key actual (prefijo `mp583k...`)
    cubre tanto enrichment (reply_agent) como gestión de secuencias
    (orchestrator). Si en el futuro se rota la key, asegurarse de marcar
    "Establecer como clave maestra" antes de crearla.

---

## Contexto de negocio

**VER-IA** (empresa de Daniel Sánchez, `dsanchez@`) es dueña de la plataforma;
Claude actúa como CTO técnico. **Separación corporativa pendiente (F1):** el
repo, la suscripción Azure y la PC de trabajo hoy pertenecen al entorno del
cliente #1 — migrar cuando exista el tenant M365/Azure/GitHub de VER-IA.

**Tenant #1 — Biodegradables Ecuador** (`@biodegradablesecuador.com`):
- Comercial/distribución de empaques biodegradables; Quito (UIO, doc `001-002`)
  y Guayaquil (GYE, doc `001-001`). ERP: Contifico.
- Personas (detalle en `tenants/biodegradables/config.yaml`): Daniel Sánchez
  (gerente, supervisor — no trackea actividades), Gabriela Sánchez (gerente
  comercial, `gsanchez@` — OJO: distinta de Gabriela Bravo `info@` GYE),
  Mateo Alvarado (`malvarado@`, opera esta PC — puede tener OTRA sesión de
  Claude abierta en paralelo), `quito@` (asistente UIO), José (chofer GYE).
- Prospección outbound vía Apollo.io (plan Basic: máx 1 secuencia activa —
  Apollo pausa las demás en silencio). Reply agent responde a los que contestan.

**Tenant #2 — Andex (demo):** empresa ficticia para demos comerciales
(`DEMO_MODE=1`, `demo_*.py`). Ver `PROPUESTA_DEMO_COMERCIAL.md`.

Última actualización: **2026-07-04** (pivot VER-IA F0–F5; este archivo se
reescribió para reflejar la plataforma — si encuentras una sección que
contradiga las "reglas vigentes" del inicio, ganan las reglas vigentes).
