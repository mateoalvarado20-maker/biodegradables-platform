# Proyecto: Reporte diario Biodegradables Ecuador

Este folder contiene el sistema automatizado que envía un resumen comercial
diario a las 8:00 AM al gerente, con datos de Power BI cloud (dataset Contifico).

**Si eres Claude en una sesión nueva:** lee este archivo antes de hacer cambios.
Toda la lógica clave está aquí.

---

## ✅ REFACTOR COMPLETADO (2026-06-12) — leer primero

Las 6 fases del refactor post-auditoría están implementadas y commiteadas
(`git log`). Diagnóstico original: `AUDITORIA_TECNICA_2026-06-12.md`.
Documentación viva: `CONTRIBUTING.md` (reglas + PR checklist),
`docs/arquitectura.md` (capas, dueños de estado, garantías y dónde se
testean), `docs/onboarding.md`, `docs/runbook-operativo.md` (incidentes).

**Reglas permanentes:**
- El proyecto es un **repo git** (`C:\Users\Mateo`, `.gitignore` whitelist). Todo cambio se commitea (idealmente por PR con el CI de `.github/workflows/ci.yml`).
- **NO editar `azfunc\` a mano** (salvo los archivos azfunc-específicos listados en `tools/sync_azfunc.py`): se GENERA con `python tools/sync_azfunc.py`. El zip del bot se genera con `python tools/build_bot_package.py`.
- Todo state pasa por `safe_json` (atómico+backup+cuarentena+locks); todo envío programado pasa por `_reliable_job` + `send_ledger` (nunca dos veces, nunca perdido en silencio); identidad SOLO desde el registro AAD (display name prohibido como fuente); config de negocio (destinatarios, feriados, PY_OVERRIDE, umbrales) SOLO en `core_config.py`/env vars; `date.today()` prohibido (usar helpers TZ Ecuador).
- Suite de tests: `python -m pytest tests/ -q` (50 tests: aislamiento entre usuarios, concurrencia, corrupción, anti-duplicado, horarios, identidad). Correrla antes de cualquier deploy.
- Código retirado vive en `archive/` con justificación en `archive/README.md`. NO restaurar sin leerla. Retirados: `weekly_report.py` (roto y huérfano — reemplazado por `weekly_summaries` del bot), `agent.py`, `apollo_orchestrator.*`, `run_reply_agent.bat`, `run_weekly_report.bat`.

> **Auditoría 2026-06-22:** ver `AUTOMATIZACIONES_EMPRESA.md` (inventario maestro de
> bots, agentes y automatizaciones + plan de migración). Esa auditoría actualizó los
> pendientes de abajo y agregó la sección "Módulos adicionales" más abajo.

**Pendientes operativos del refactor (acción humana, ver runbook):**
1. ✅ **HECHO (2026-06-22):** repo conectado a GitHub (`mateoalvarado20-maker/biodegradables-platform`), 6 PRs mergeados. Falta solo confirmar branch protection en `master`.
2. Deploy del bot (`tools/build_bot_package.py` → `az webapp deploy`) y del azfunc sincronizado.
3. ✅ **HECHO (2026-07-02, F0 VER-IA):** `ADMIN_API_TOKEN` propio seteado en el App Service (random 32 bytes; también en env var User de esta PC para scripts de testing). El código además eliminó el fallback al secret OAuth (fail-closed). Dead-man switch desplegado: webtest `webtest-bot-deadman` + action group `ag-veria-alertas` + alerta en `rg-biodegradables-prod` — tras deployar F0, cambiar la URL del webtest de `/health` a `/health/deliveries`.
4. Cutover de logística al bot (`LOGISTICS_IN_BOT=1` + disable del timer azfunc, en la misma ventana — runbook §Cutover).
5. (Opcional) `DISPATCH_TABLE_CONN` en la PC para que `dispatch.py` escriba a la tabla de producción.

**Estado operativo REAL verificado (2026-06-12):**
| Qué | Dónde corre HOY |
|---|---|
| Reporte comercial 8 AM | Job APScheduler `morning_sales_report` en teams_bot (App Service). Timer azfunc ELIMINADO del código en Fase 0. Schtask local DESHABILITADA (run_morning.bat queda para runs manuales). |
| Reporte logística 8 AM | Timer azfunc `logistics_morning`. Schtask local DESHABILITADA. |
| Reply agent cada 15 min | Timer azfunc `reply_agent_tick` (state en Azure Table). Schtask local DESHABILITADA y wrapper archivado — re-habilitarla duplicaría borradores. |
| Apollo notifier cada 2 h | Schtask local `BiodegradablesEcuador-ApolloNotifier-2hrs` (única tarea local activa). ⚠️ SPOF: depende de que la PC de Mateo esté encendida; cuando la PC se suspende a la hora del trigger, Task Scheduler reporta `LastTaskResult 0xC000013A` (proceso abortado) aunque el script en sí termina en exit 0. Candidato a migrar a un timer de Azure Functions. |
| Weekly report de Mateo | Job `weekly_summaries` del bot (Vie 17:00). El `weekly_report.py` viejo está archivado — NO crear su schtask. |

---

## Stack

- Python 3.14 (Windows Store), instalado para el usuario `Mateo`
- Librerías: `anthropic`, `mcp`, `msal`, `httpx`
- Auth: Microsoft Entra ID via MSAL device-code (token cache persistente)
- Fuente de datos: Power BI REST API (`/datasets/{id}/executeQueries`)
- Envío: Microsoft Graph API (`/me/sendMail`)

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
| `teams_bot.py` | **[En desarrollo — Phase B]** Backend FastAPI + Bot Framework para chat bot en Teams. Reusa `ask_agent.ask()`. Whitelist de usuarios. Comandos `/help` y `/refresh`. Espera env vars `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `MICROSOFT_APP_TENANT_ID`. Listo para deploy cuando Azure esté activo. |
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
| `apollo_completion_notifier.py` | **Notificador de secuencias completadas**. Cada 2 horas chequea las secuencias activas y envía correo a malvarado@ cuando una llega a `unique_scheduled == 0` (terminó su cola). Incluye stats finales + **sugerencia IA de filtros Apollo** (industria, cargo, ubicación, keywords) generada con Claude sonnet-4-6 leyendo `company_context.md` como contexto del producto. State en `~/.claude-agent/apollo_completion_state.json` para evitar duplicados. CLI: `--status`, `--dry-run`, `--reset SEQ_ID`. Requiere `APOLLO_API_KEY` master + `ANTHROPIC_API_KEY`. |
| `run_apollo_notifier.bat` | Wrapper Task Scheduler del notificador. Logs en `logs/apollo-notifier-AAAAMMDD.log`. |
| `activities_template.json` | Plantilla de actividades recurrentes que Mateo debe ejecutar cada semana (Apollo 70 correos/día, TikTok video+live, códigos Contifico, chatbots, etc.). Se lee SOLO al inicializar una semana nueva en `activity_state.py`. Editable a mano para agregar/quitar/ajustar metas. |
| `activity_state.py` | Persistencia del tracker de actividades semanales. State en `~/.claude-agent/activity_state.json`, una entry por semana ISO (`AAAA-Www`). Funciones: `init_week`, `mark_daily`, `set_weekly_progress`, `add_adhoc`, `remove_activity`, `daily_total`, `daily_compliance`. |
| `activity_tracker.py` | CLI para tracking de actividades (estilo `dispatch.py`). Subcomandos: `done` (valor diario), `progress` (% avance semanal), `add` (ad-hoc), `remove`, `status`/`week`. Para uso de Mateo durante la semana mientras se implementa el bot de Teams en Fase 2. |
| `weekly_report.py` | **Reporte semanal de actividades a Daniel los viernes 5 PM**. Lee `activity_state.json` y envía HTML con KPIs (correos Apollo, respuestas, avance proyectos), tabla de actividades diarias con columna por día + cumplimiento, tabla de proyectos semanales con avance %, y sección de pendientes. Modos: `send` (a JEFE), `test` (solo Mateo), `dry` (stdout), `preview --wk` (semana específica). |
| `run_weekly_report.bat` | Wrapper Task Scheduler viernes 5 PM. Logs en `logs/weekly-AAAAMMDD.log`. |
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
| `apollo_stats.py` | Métricas de prospección Apollo (enviados/respuestas). **HUÉRFANO**: ningún módulo lo importa | ⚠️ Sin uso |
| `wp_client.py` | Cliente REST de WordPress (Basic Auth + Application Password). Solo lectura habilitada. Base de los `wp_*` | ✅ Activo |
| `wp_audit.py` / `wp_check.py` / `wp_drafts.py` | Auditoría/smoke-test/inspección del WordPress. CLIs manuales, no automatizados | 🔧 Manual |
| `wp_apply.py` | Aplicación controlada de cambios al WordPress. Dry-run por defecto; exige `--apply --approve <id>`; guarda backups | 🔧 Manual |
| `graph_mail.py` | Envío de correo vía Service Principal (app-only, client_credentials). NO usa MSAL. Usado por todos los reportes del bot/azfunc | ✅ Activo |
| `core_config.py` | **Fuente única** de config de negocio: destinatarios, feriados EC (2025-2027), `META_FACTOR`, `PY_OVERRIDE` (keyed por (año,mes)), umbrales, horarios de check-in | ✅ Activo |
| `safe_json.py` / `send_ledger.py` | Infraestructura: escritura atómica + backup + cuarentena + locks; ledger anti-duplicado de envíos | ✅ Activo |

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

## Constantes clave en `daily_report.py`

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

## Esquema del modelo Power BI (Contifico)

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

### 1. Reporte diario
- **Nombre:** `BiodegradablesEcuador-DailyReport-Morning`
- **Trigger:** Diario a las 8:00 AM hora local Ecuador
- **Acción:** `C:\Users\Mateo\run_morning.bat`
- **Logs:** `C:\Users\Mateo\logs\morning-AAAAMMDD.log`

### 2. Reply agent (respuestas automáticas a prospectos)
- **Nombre:** `BiodegradablesEcuador-ReplyAgent-15min`
- **Trigger:** Cada 15 minutos
- **Acción:** `C:\Users\Mateo\run_reply_agent.bat`
- **Logs:** `C:\Users\Mateo\logs\reply-AAAAMMDD.log`

### 3. Reporte logística (envíos a Gabriela)
- **Nombre:** `BiodegradablesEcuador-LogisticsReport-Morning`
- **Trigger:** Diario a las 8:00 AM hora local Ecuador
- **Acción:** `C:\Users\Mateo\run_logistics.bat`
- **Logs:** `C:\Users\Mateo\logs\logistics-AAAAMMDD.log`
- **Estado:** REQUIERE `CONTIFICO_API_TOKEN` configurado. NO crear la tarea hasta probar con `python daily_logistics_report.py dry` y validar resultados.

Comando para crearla (cuando el token esté configurado y se haya validado un run en modo `test`):
```powershell
schtasks /create /tn "BiodegradablesEcuador-LogisticsReport-Morning" `
  /tr "C:\Users\Mateo\run_logistics.bat" `
  /sc daily /st 08:00 /ru "Mateo" /rl LIMITED /f
```

### 4. Apollo orchestrator (rotación de secuencias)
- **Estado Task Scheduler local: ELIMINADO 2026-05-28.** El user lo descartó (limitaba volumen). Archivos `.py/.json/.bat` preservados pero tarea programada eliminada.
- **Estado Azure Functions: DESHABILITADO 2026-06-01.** ⚠️ El orquestador ALSO vivía en `func-biodegradables-ec` (folder `azfunc/`) como timer `apollo_orchestrator_tick` cada 30 min. Se deshabilitó via app setting:
  ```
  AzureWebJobs.apollo_orchestrator_tick.Disabled=true
  ```
- **LECCIÓN APRENDIDA:** muchas tareas vivían en Task Scheduler local Y en Azure Functions (`azfunc/`). Antes de declarar algo "desactivado", chequear AMBOS lugares. Otros timers Azure activos: `logistics_morning` (8 AM EC), `reply_agent_tick` (cada 15 min).

### 5. Apollo completion notifier (avisa cuando termina una secuencia)
- **Nombre:** `BiodegradablesEcuador-ApolloNotifier-2hrs`
- **Trigger:** Cada 2 horas
- **Acción:** `C:\Users\Mateo\run_apollo_notifier.bat`
- **Logs:** `C:\Users\Mateo\logs\apollo-notifier-AAAAMMDD.log`
- **Estado:** OPERATIVO desde 2026-05-28 12:40. Genera sugerencia IA de filtros Apollo (industria, cargo, ubicación, keywords) usando Claude sonnet-4-6 y `company_context.md` como contexto.

### 5. Reporte semanal de actividades de Mateo (a Daniel)
- **Nombre:** `BiodegradablesEcuador-WeeklyActivityReport-Friday`
- **Trigger:** Viernes 17:00 hora local Ecuador
- **Acción:** `C:\Users\Mateo\run_weekly_report.bat`
- **Logs:** `C:\Users\Mateo\logs\weekly-AAAAMMDD.log`
- **Estado:** Listo para crear. Por validar primero con `python weekly_report.py test`.

Comando para crearla (cuando esté validado):
```powershell
schtasks /create /tn "BiodegradablesEcuador-WeeklyActivityReport-Friday" `
  /tr "C:\Users\Mateo\run_weekly_report.bat" `
  /sc weekly /d FRI /st 17:00 /ru "Mateo" /rl LIMITED /f
```

Comandos útiles:
```powershell
# Estado de las tareas
Get-ScheduledTask -TaskName "BiodegradablesEcuador-DailyReport-Morning" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "BiodegradablesEcuador-ReplyAgent-15min" | Get-ScheduledTaskInfo
Get-ScheduledTask -TaskName "BiodegradablesEcuador-ApolloOrchestrator-30min" | Get-ScheduledTaskInfo

# Forzar ejecución
schtasks /run /tn "BiodegradablesEcuador-DailyReport-Morning"
schtasks /run /tn "BiodegradablesEcuador-ReplyAgent-15min"
schtasks /run /tn "BiodegradablesEcuador-ApolloOrchestrator-30min"

# Ver logs más recientes
Get-Content C:\Users\Mateo\logs\morning-*.log -Tail 30
Get-Content C:\Users\Mateo\logs\reply-*.log -Tail 30
Get-Content C:\Users\Mateo\logs\apollo-*.log -Tail 30

# Probar reply_agent en seco (sin crear drafts)
python reply_agent.py --dry-run --since-hours 24 --verbose

# Apollo orchestrator
python apollo_orchestrator.py --status        # ver qué secuencia está activa y cuál sigue
python apollo_orchestrator.py --dry-run       # qué haría sin ejecutar
python apollo_orchestrator.py --pause-all     # pausar todas (para emergencias)
python apollo_orchestrator.py --force-rotate  # forzar paso a la siguiente
```

---

## Cómo responder preguntas sobre Power BI

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

`reply_agent.py` es el sistema de respuestas automáticas a prospectos. Corre
cada 15 min vía Task Scheduler. Flujo:

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
8. Persiste el `message_id` en `~/.claude-agent/reply_state.json` para no
   duplicar borradores en la siguiente corrida

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

## Datos del usuario / contexto de negocio

- **Empresa:** Biodegradables Ecuador (`@biodegradablesecuador.com`)
- **Industria:** comercial / distribución
- **Ciudades operativas:** Quito (UIO) y Guayaquil (GYE)
- **ERP fuente:** Contifico (los datasets de PBI extraen de ahí)
- **Producto del reporte:** dashboard "COM-RPT-001 Dashboard Comercial Contifico"
  con páginas: Seguimiento Mensual de Ventas, Ventas Históricas, Ventas por
  Productos, Ventas por Clientes, Cobranzas – Gestión de Riesgo
- **Gerente:** Daniel Sánchez (`dsanchez@`)
- **Gerente comercial:** Gabriela Sánchez (`gsanchez@`, +593 98 042 8767) — actualmente NO se menciona en reply agent (ver Issue #9). Recibe el reporte de logística diario.
- **Usuario:** Mateo Alvarado (`malvarado@`)
- **Prospección outbound:** vía Apollo.io (plan Basic). Reply agent responde automáticamente a los que contestan. **Orquestador de secuencias** (`apollo_orchestrator.py`) mantiene UNA sola secuencia activa a la vez para evitar cola amontonada de envíos.

Última actualización: 2026-05-21
