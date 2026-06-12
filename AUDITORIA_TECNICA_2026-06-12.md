# Auditoría Técnica Completa — Sistema Biodegradables Ecuador

**Fecha:** 2026-06-12
**Alcance:** todo el código en `C:\Users\Mateo\` + `azfunc\` + configuración real de Azure (verificada con `az` CLI) + Task Scheduler local + logs de producción.
**Método:** 5 auditorías especializadas en paralelo (aislamiento del Activities Bot, scheduling/entrega, persistencia/concurrencia, reportes/correo, clientes API/agentes IA) + verificación directa de infraestructura. Todos los hallazgos tienen evidencia `archivo:línea`. **No se modificó ningún archivo.**

---

## 1. Resumen ejecutivo

El sistema funciona, pero su confiabilidad descansa en convenciones no verificadas (una sola instancia, un solo escritor, env vars presentes, nadie re-habilita una tarea vieja). Los síntomas reportados tienen causas raíz identificadas y verificadas:

| Síntoma reportado | Causa raíz verificada |
|---|---|
| "Las actividades se mezclan entre usuarios" | (1) Resolución de identidad por display name con alias de 1 palabra: cualquier "Gabriela *" del tenant resuelve a `gsanchez@`. (2) Pseudo-usuario compartido `unidentified-unknown@`. (3) Lost updates por escritura concurrente sin lock al state. (4) Submits de cards viejos escriben en la fecha/semana de hoy. |
| "Errores de ejecución inesperados" | 40+ `except Exception` silenciosos; JSON corrupto se "recupera" como estado vacío y se persiste vacío (wipe total enmascarado); reportes salen con $0 cuando Contifico falla, con exit 0. |
| "Problemas de sincronización" | Universos de estado paralelos: el CLI local escribe archivos que el bot en Azure jamás lee (dispatch, activity_state); 8 de 13 módulos duplicados raíz↔azfunc ya divergieron. |
| "Se rompe cuando colaboradores tocan el código" | **No hay repositorio git.** No hay tests (solo `test_ventas.py`, un script manual). Tres copias del código (raíz, `azfunc\`, `bot_deploy_stage\`) que se sincronizan a mano. Config crítica hardcodeada en 2–4 sitios por valor. |

**Las 5 acciones de mayor impacto** (detalle en §6):
1. `git init` + GitHub privado + CI. Sin esto, todo lo demás es frágil.
2. Endurecer la capa de state: escritura atómica + lock + cuarentena de archivos corruptos (elimina el riesgo de wipe total y los lost updates).
3. Eliminar la resolución de identidad por display name para autorización; allowlists fail-closed.
4. Embeber `user_email`/`fecha`/`semana` en los Adaptive Cards y validarlos al submit.
5. Ledger de "ya enviado" por (reporte, fecha) + alerta a un humano cuando un reporte NO sale o sale degradado.

---

## 2. Diagnóstico detallado por área

### 2.1 Activities Bot — aislamiento de datos entre usuarios

| # | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| A1 | CRÍTICA | **Identidad por display name con alias de 1 palabra.** `_match_name_to_collaborator` exige que las palabras del alias sean subconjunto del display name. Alias `gabriela` ⊆ "Gabriela Bravo" → resuelve a `gsanchez@`. Consecuencia en cadena: sus marcas caen en el state de Gabriela Sánchez, **y su conversation reference sobrescribe el de gsanchez@** → el check-in de las 16:30 llega al chat equivocado y la contaminación se vuelve bidireccional y persistente. Mismo riesgo con alias `quito`/`gye`/`info` ("Bodega Quito"). | `teams_bot.py:189-229`, `:303-398`, `:1514` |
| A2 | CRÍTICA | **RMW sin lock con escritores concurrentes reales.** `load()` → mutar → `save()` del JSON completo, sin lock ni escritura atómica. Escriben en paralelo: handlers async (event loop), tools de `ask_agent` vía `asyncio.to_thread` (worker threads), y jobs de APScheduler. Un check-in de quito@ y un mensaje de texto de info@ a la misma hora ⇒ el `save()` más lento pisa al otro: **marcas que desaparecen sin error**. | `activity_state.py:74-94`, `teams_bot.py:1826-1828`, `:3792` |
| A3 | CRÍTICA | **JSON corrupto ⇒ wipe silencioso total.** Crash/recycle a mitad de `write_text` deja el archivo truncado; el siguiente `load()` devuelve `{"users": {}}` sin error y el siguiente `save()` **persiste el vacío**: semanas de todos los colaboradores borradas sin un solo log. Aplica también a `reminders.json`, `conversation_refs.json`, `aad_lookup.json`, `conversation_history.json`. | `activity_state.py:77-80`, `reminders.py:65-68`, `teams_bot.py:136-138` |
| A4 | ALTA | **Pseudo-usuario compartido `unidentified-unknown@`**: si falta el AAD object id, TODOS los no-resueltos comparten state, historial y ref de conversación. | `teams_bot.py:396` |
| A5 | ALTA | **El submit del card no lleva contexto.** El card solo embebe `{"intent": "submit_checkin"}`; fecha, semana y usuario se re-derivan AL SUBMIT. Card del viernes llenado el lunes ⇒ marcas en la semana nueva; cierre de caja de ayer registrado como de hoy. Los cards viejos quedan vivos en Teams indefinidamente. | `teams_bot.py:1191-1196`, `:1232`, `:1272` |
| A6 | ALTA | **`_resolve_collaborator` acepta cualquier string con `@`**: una asignación de Daniel a un email tipeado mal por el LLM crea un usuario fantasma y la actividad "desaparece" sin error. | `ask_agent.py:87-94` |
| A7 | ALTA | **Fallback silencioso a Mateo:** toda función del state con email vacío escribe en el bucket de `malvarado@`. | `activity_state.py:53-66` |
| A8 | ALTA | **Endpoint admin muta `os.environ` permanentemente:** un test del consolidado deja `CONSOLIDATED_DAILY_TO` apuntando a los destinatarios del test hasta el próximo restart. | `teams_bot.py:4730-4733` |
| A9 | MEDIA | **Mezcla de zonas horarias:** conviven `date.today()` (UTC en Azure) y `_today()` (UTC-5). Entre 19:00–23:59 EC, el path de texto natural marca actividades en la fecha de MAÑANA mientras el card-submit usa la fecha correcta. | `teams_bot.py:655-657`, `:1910`, `ask_agent.py:97-99` |
| A10 | MEDIA | **Cobranzas auto-asignadas con `tipo="unica"` nunca renderizan la UI de contactado/no-contactado** (ese código es efectivamente dead code) — caen como "proyecto semanal" con input de % avance. | `teams_bot.py:1937-1941`, `:651-653`, `:816-843` |
| A11 | MEDIA | **`confirmar_cierre` y los intents `jose_*` confían en el payload del card** sin validar quién lo envía: cualquier usuario del tenant puede escribir confirmaciones de cierres ajenos. | `teams_bot.py:1608-1656`, `:1525` |
| A12 | MEDIA | **Validación débil:** valor "0" en estado "hecho" acredita la meta completa; `fecha` es string libre (Claude puede pasar cualquier cosa); errores por actividad se tragan con `logger.warning` y el usuario ve "✅ Marcadas N". | `teams_bot.py:1302-1306`, `:1324-1325`, `activity_state.py:192-231` |
| A13 | BAJA | Semana previa hardcodeada `prev_w=52` (2026 tiene 53 semanas ISO); check-in de sábado infla `daily_compliance` calculado sobre 5 días; supervisores no excluidos del path de texto. | `activity_state.py:1048-1053`, `:1094-1105`, `teams_bot.py:1767-1772` |

### 2.2 Scheduling y entrega de reportes

**Estado real verificado (2026-06-12):**
- Task Scheduler local: solo `ApolloNotifier-2hrs` ACTIVA. DailyReport, Logistics y ReplyAgent **deshabilitadas**. WeeklyActivityReport **nunca se creó**.
- Azure Functions: `morning_sales_report` y `apollo_orchestrator_tick` deshabilitados vía app setting (✅ verificado con `az`). `logistics_morning` y `reply_agent_tick` ACTIVOS.
- App Service del bot: P0v3, **1 instancia**, `STATE_DIR=/home/.claude-agent` (persistente ✅).

| # | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| S1 | ALTA | **El reporte comercial sigue DEFINIDO en dos capas.** El timer azfunc está apagado solo por app setting — si alguien lo borra o redeploya a un app nuevo, **revive solo** y Daniel recibe dos correos con números distintos (las copias divergieron). Mismo patrón latente con `apollo_orchestrator_tick`. | `azfunc\function_app.py:43-62` vs `teams_bot.py:3920-3925` |
| S2 | ALTA | **APScheduler con jobstore en memoria y `misfire_grace_time=1s`:** un deploy/restart a la hora exacta de un job = ejecución perdida en silencio. Peor caso: restart el día 1 a las 9:00 ⇒ el recap mensual se pierde **un mes entero**. | `teams_bot.py:3811`, `:3940-3946` |
| S3 | ALTA | **Sin protección single-instance:** si el App Service escala a 2, dos APSchedulers duplican TODOS los correos y cards. Hoy funciona por convención (capacity=1), no por diseño. | `teams_bot.py:3940-3946` |
| S4 | ALTA | **Recaps mensuales sin wrapper de error, sin retry, sin alerta** (todos los demás jobs tienen try/except; estos dos no). | `teams_bot.py:3868-3879` |
| S5 | ALTA | **`logistics_morning` (única fuente del reporte de Gabriela) vive en el plan Consumption que "se dormía"** (motivo por el cual el comercial ya fue migrado al bot), sin retry policy en `host.json` y sin alerta si falla. | `azfunc\function_app.py:21-40` |
| S6 | ALTA | **`weekly_report.py` huérfano Y roto:** nadie lo dispara y llama `get_week(wk)` con la firma vieja (hoy interpretaría la semana como email). Superseded por `weekly_summaries` del bot. CLAUDE.md aún dice "listo para crear" — si alguien crea la tarea, manda un reporte roto. | `weekly_report.py:124` vs `activity_state.py:186-189` |
| S7 | MEDIA | **4 de 5 `.bat` usan `wmic` (removido en Win11 24H2, este equipo es build 26200):** el nombre del log se corrompe y el output se pierde — incluido el de la ÚNICA tarea local activa (ApolloNotifier). Además **ningún .bat propaga el exit code**: Task Scheduler siempre ve 0x0. Verificado en logs reales: los `apollo-notifier-*.log` recientes solo contienen el header de fecha, nada del output de Python. | `run_apollo_notifier.bat:9`, `run_logistics.bat:8`, `run_weekly_report.bat:10`, `run_reply_agent.bat:10` |
| S8 | MEDIA | **ApolloNotifier depende del PC encendido + refresh token MSAL de 90 días** con `interactive_ok=False`: cuando venza, fallará cada tick en silencio (log roto por S7). Puede estar semanas muerto sin que nadie lo note. | `apollo_completion_notifier.py:295-310`, `pbi_cloud.py:109` |
| S9 | MEDIA | **Ningún reporte persiste "ya enviado hoy":** el retry del morning (único job con retry+alerta) puede duplicar si Graph aceptó el correo pero la respuesta se perdió. | `teams_bot.py:3548-3580` |
| S10 | MEDIA | **`deliver_due_reminders`:** si `reschedule_recurring` falla DESPUÉS de `mark_sent`, la serie recurrente muere para siempre sin aviso; lost update con creación de reminders desde threads re-entrega y bifurca series. | `teams_bot.py:1992-2012`, `reminders.py:230-238` |
| S11 | BAJA | Offsets UTC-5 hardcodeados dispersos (vs `America/Guayaquil` correcto en el bot); `logistics_morning` y `daily_news_brief` corren los 7 días sin skip de domingo. | `reminders.py:45`, `news_brief.py:30`, `function_app.py:22` |

### 2.3 Persistencia y universos de estado paralelos

**No existe NINGÚN lock ni NINGUNA escritura atómica en todo el proyecto** (grep verificado: cero usos de `filelock`/`msvcrt`/`portalocker`/`os.replace`).

| # | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| P1 | CRÍTICA | **Split-brain de dispatch:** el CLI local escribe `~/.claude-agent/dispatch_state.json` en el PC; el reporte de logística en producción (Azure Functions) lee **Azure Table Storage**. Lo marcado con `dispatch.py` **jamás aparece** en el correo de Gabriela. Además `teams_bot.py` no importa dispatch_state en absoluto — la "Fase 2 por Teams" no escribe a ningún universo. | `dispatch_state.py:30` vs `azfunc\dispatch_state.py:39-55` |
| P2 | ALTA | **`activity_state.json` también es dos universos:** el bot escribe en `/home/.claude-agent` de Azure; `activity_tracker.py` CLI, `weekly_report.py` y `monthly_recap.py` locales leen/escriben el archivo del PC (verificado: el local quedó congelado el 31/5, el bot siguió funcionando). Lo del CLI no existe para el bot y viceversa. | `activity_state.py:47`, `activity_tracker.py:35` |
| P3 | ALTA | **Reply agent: dos implementaciones con states disjuntos** (local JSON / Azure Table). Hoy la tarea local está deshabilitada — el riesgo es latente: re-habilitarla = borradores duplicados a prospectos, porque ninguna versión marca el correo como leído en Graph. | `reply_agent.py:40` vs `azfunc\reply_state.py` |
| P4 | MEDIA | **`save()` de azfunc dispatch lanza `NotImplementedError` en Azure** — el patrón compartido `load();...;save()` funciona local y crashea en producción; `get()`/`clear()` tragan excepciones de Table Storage devolviendo "no marcado" (error de infra disfrazado de dato). | `azfunc\dispatch_state.py:96-104`, `:159-160` |
| P5 | MEDIA | **Recálculo de cierre de caja sin sucursal:** `ask_agent` recalcula con fondo fijo $50; Guayaquil usa $100 ⇒ dos números distintos para el mismo cierre según la pantalla. | `ask_agent.py:762` vs `activity_state.py:572-582` |
| P6 | MEDIA | **Esquema implícito sin validación:** migraciones al vuelo en `load()`, claves opcionales por fase, accesos directos que lanzan `KeyError` con states viejos. El "esquema" real es la unión de todos los caminos de código que alguna vez escribieron. | `activity_state.py:81-83`, `reminders.py:205` |
| P7 | BAJA | Cache de Apollo en memoria cargado una vez por proceso, reescrito completo (último escritor gana, sin purga ni tope); `reply_state` recorta a 500 IDs. | `apollo_rest.py:32-84` |

### 2.4 Reportes, correo y drift de copias

**Drift verificado por hash MD5 (8 de 13 pares divergieron):**

| Par raíz ↔ azfunc | Delta clave |
|---|---|
| `graph_mail.py` | Raíz (Phase V): 4 retries con backoff + refresh en 401 + logging. **Azfunc: cero retries, un solo POST** — el fix nunca se propagó. |
| `contifico_client.py` | Raíz: cartera por `fecha_vencimiento` + filtro $1 + fix de "TRANSP" abreviado. **Azfunc sigue en Phase R: bug de "TRANSPORTE" vivo, sin filtro de centavos** ⇒ el correo de las 8 AM usa la metodología vieja. **Lo que Mateo valida localmente NO es lo que recibe Daniel.** |
| `daily_logistics_report.py` | Idénticos salvo el backend de correo (pbi_cloud vs graph_mail). |
| `dispatch_state.py` | Backends distintos (ver P1). |
| `reply_agent.py`, `apollo_rest.py`, `outlook_client.py`, `apollo_orchestrator.py` | Divergencias menores acumuladas. |

| # | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| R1 | CRÍTICA | **El reporte diario sale con $0 si Contifico falla, sin que nadie se entere.** `_safe` traga toda excepción → `float(... or 0)` → correo "Vendimos ayer $0, cumplimiento 0%" con exit 0 y "[OK] Enviado" en el log. Datos falsos presentados como reales al gerente. | `daily_report.py:383-391`, `:584`, `:604` |
| R2 | ALTA | **El fallback MSAL de `daily_report.py` es código muerto** (el comentario sobre `send` vs `send_email` ya no es cierto): un run local sin las env vars del Service Principal falla en vez de caer a MSAL. | `daily_report.py:30-40` |
| R3 | ALTA | **`monthly_recap` puede crashear** (`{pace_pct:.0f}` con `pace_pct=None`) y tiene 6 `except Exception` que producen un recap "$0" enviado como real. | `monthly_recap.py:334`, `:432`, `:77-104` |
| R4 | ALTA | **Truncamiento silencioso de paginación Contifico:** `if page > 100: break` sin log — totales subestimados sin señal en rangos largos. | `contifico_client.py:146-147` (×2 copias) |
| R5 | MEDIA | **Definiciones de "vencido" inconsistentes en el MISMO correo:** KPIs de cartera usan `condiciones_credito.json`, top deudores usan `fecha_vencimiento` ⇒ no cuadran entre sí. | `contifico_client.py:566-596` vs `:493-563` |
| R6 | MEDIA | "Ayer" inconsistente: ventas usa `previous_workday()`, exportación usa `today-1` (lunes: ventas del sábado + exportación del domingo). | `daily_report.py:473` vs `:701` |
| R7 | MEDIA | **`_DOCS_CACHE` sin TTL en proceso long-running:** el Data Bot responde "¿cuánto vendimos hoy?" a las 17:00 con datos cacheados de las 9:00, mientras el system prompt afirma que son "en vivo". Crece sin tope. | `contifico_client.py:42-48`, `ask_agent.py:3007` |
| R8 | MEDIA | **`PY_OVERRIDE` keyed por mes sin año** (se re-aplicará en mayo 2027/2028) y duplicado en 4 sitios; `EC_HOLIDAYS` en ≥4 sitios y **falta 2027** (en enero los días hábiles se calcularán sin feriados, silenciosamente). | `daily_report.py:150-153`, `contifico_client.py:57-77` |
| R9 | MEDIA | **Heurísticas frágiles sin marca de duda:** provincia por substring sin word-boundary ("coca" → Orellana), fallback al origen si no parsea, prefijo `001-003` futuro caería a "?", `fmt_pct` (8% real → 800%), `_as_ratio` (2.5 → 0.025). | `daily_logistics_report.py:87-210`, `daily_report.py:195-261` |
| R10 | BAJA | Para cambiar un destinatario hay que tocar **hasta 4 archivos** (JEFE/MIO/GABRIELA duplicados raíz+azfunc); solo `monthly_recap` es configurable por env var. Código muerto: `html_eod`, `q_ventas_dia`, `_collaborator_block_html` (~190 líneas), `agent.py` entero. | `daily_report.py:209-213`, `daily_logistics_report.py:38-39` |

### 2.5 Clientes API, agentes IA y seguridad

| # | Sev. | Hallazgo | Evidencia |
|---|---|---|---|
| C1 | ALTA | **Allowlist del Data Bot fail-open:** si `BOT_ALLOWED_USERS_DATA` falta/queda vacía en un redeploy, **cualquier usuario del tenant** accede a ventas, cartera y tools de gerencia. Combinado con A1 (suplantación por display name), la frontera de autorización es débil en ambas capas. | `teams_bot.py:401-404` |
| C2 | ALTA | **Error de Apollo (429/5xx/créditos agotados) se confunde con "no es prospecto"** y el correo se marca procesado PARA SIEMPRE: durante un outage de 1 hora, todos los prospectos reales que escriban quedan sin borrador, sin alerta. | `apollo_rest.py:141-149`, `reply_agent.py:286-291` |
| C3 | MEDIA | **JSON inválido de Claude = skip permanente del prospecto** (no se distingue "decidió no responder" de "falló el formato"). | `reply_agent.py:219-231`, `:314-318` |
| C4 | MEDIA | **Prompt injection → tools con efectos:** nombres de clientes Contifico / leads HubSpot / resultados de web_search fluyen al modelo sin sanitizar, en un modo que tiene `schedule_reminder_for_collaborator` y `add_activity_for_collaborator`, y `_resolve_collaborator` acepta emails arbitrarios. Mitigación parcial: nada envía correo externo directo. | `ask_agent.py:3118-3120`, `:87-94` |
| C5 | MEDIA | **El secret OAuth del bot (`MICROSOFT_APP_PASSWORD`, con `Mail.Send` application-wide) se reusa como admin token** en ~30 endpoints, comparación no constante, viaja como header en cada test. | `teams_bot.py:3995-3997` |
| C6 | MEDIA | **Filtro del reply agent incompleto:** "Automatic reply:"/"Respuesta automática:" no filtrados; no verifica que Mateo esté en To:. Mitigación: crea drafts, no envía. | `reply_agent.py:35-38`, `:79-81` |
| C7 | MEDIA | HubSpot: `leads_sin_responder` devuelve `{"count": 0}` ante CUALQUIER excepción (un 401 se reporta como "0 leads"); token capturado en import (rotar = restart). | `hubspot_client.py:383-384`, `:21` |
| C8 | BAJA | `ask_agent.py`: 3.570 líneas, ~63% es generación de HTML de correo con 4 variantes de la misma lógica de tablas (~25-30% redundante o muerto); truncamiento de tool_result a 2500 chars puede cortar JSON a la mitad; retry de Anthropic solo cubre 429 con sleeps bloqueantes de hasta 90s en el worker thread. | `ask_agent.py:178-2435`, `:3532-3534`, `:3473-3502` |
| C9 | ✅ | **Secretos: limpio.** No hay keys hardcodeadas; todo por env vars; los logs no imprimen tokens. | — |

### 2.6 Proceso de desarrollo (causa raíz transversal)

| # | Sev. | Hallazgo |
|---|---|---|
| D1 | CRÍTICA | **No hay control de versiones.** `C:\Users\Mateo` no es repo git. No hay historial, no hay diffs, no hay rollback, no hay PRs. Cada cambio de un colaborador es irreversible e in-auditable. Esta es la causa raíz #1 de "se rompe cuando alguien toca el código". |
| D2 | CRÍTICA | **Tres copias del código sincronizadas a mano** (raíz / `azfunc\` / `bot_deploy_stage\`): 8 de 13 módulos compartidos ya divergieron; bugs corregidos en una copia siguen vivos en la que corre en producción. |
| D3 | ALTA | **Cero tests automatizados** (solo `test_ventas.py`, un script manual sin asserts ejecutables en CI). Ningún cambio se valida antes de llegar a producción. |
| D4 | MEDIA | **CLAUDE.md desactualizado en puntos operativos** (dice que DailyReport local está activa — está disabled; weekly_report "listo para crear" — está roto). La doc es el único mecanismo de coordinación y miente. |

---

## 3. Lista priorizada de riesgos

**P0 — puede causar pérdida de datos o datos falsos a gerencia HOY:**
1. Wipe silencioso total del activity_state ante JSON truncado (A3 + escritura no atómica A2). Probabilidad media (recycles de App Service son rutina), impacto catastrófico.
2. Contaminación de identidad por display name (A1) — ya ocurrió (caso info@/"Biodegradables Ecuador"); volverá a ocurrir con cualquier usuario nuevo del tenant.
3. Reporte comercial/recap con $0 presentado como dato real (R1, R3, C7).
4. Lost updates de marcas de check-in bajo concurrencia (A2, S10) — el síntoma que el equipo ya percibe.
5. Sin git ni tests (D1, D3): cada cambio nuevo puede introducir cualquiera de los anteriores sin detección.

**P1 — fallos de entrega y seguridad latentes:**
6. Split-brain dispatch (P1) — la columna "Estado" del reporte de logística es ficción para lo marcado por CLI.
7. Jobs que se pierden en deploys/restarts sin catch-up ni alerta (S2, S4); logistics sin retry en el plan que se duerme (S5).
8. Allowlist fail-open + admin token = secret del bot (C1, C5).
9. Timer duplicado del comercial protegido solo por un app setting (S1).
10. Drift raíz↔azfunc: producción corre código viejo con bugs ya corregidos (D2).
11. Prospectos perdidos para siempre ante outage de Apollo (C2).
12. Logging local roto por `wmic` + exit codes no propagados (S7) — fallos invisibles.

**P2 — calidad y mantenibilidad:**
13. Submits tardíos de cards (A5), timezone mixto (A9), validación de inputs (A12).
14. Config hardcodeada multiplicada (R8, R10), heurísticas sin marca de duda (R9), cachés sin TTL/tope (R7, P7).
15. Código muerto y duplicación masiva de HTML en ask_agent (C8), weekly_report roto (S6), agent.py (R10).

---

## 4. Propuesta de arquitectura mejorada

### 4.1 Principio rector
Pasar de "scripts que comparten archivos por convención" a "una plataforma con un solo origen de código, un solo dueño por dato, y verificación automática". Sin reescribir el sistema: es una serie de endurecimientos incrementales sobre lo que ya funciona.

### 4.2 Código: monorepo con un solo origen
```
biodegradables-platform/          (repo git, GitHub privado)
├── core/                         ← LIBRERÍA COMPARTIDA ÚNICA (pip install -e)
│   ├── clients/                  contifico, hubspot, apollo, graph_mail, outlook, calendar
│   ├── state/                    capa de persistencia endurecida (ver 4.3)
│   ├── identity/                 resolución AAD→email, colaboradores, autorización
│   ├── config/                   destinatarios, feriados, overrides, umbrales (un solo lugar, env-overridable)
│   └── reporting/                builders HTML compartidos, ledger de envíos
├── apps/
│   ├── teams_bot/                App Service (bot + APScheduler)
│   ├── functions/                Azure Functions (solo lo que deba vivir ahí)
│   ├── reports/                  daily_report, logistics, monthly_recap, news_brief
│   └── agents/                   ask_agent (tools separadas del HTML), reply_agent
├── tests/                        unit + integration + e2e
├── .github/workflows/ci.yml      lint + typecheck + tests en cada PR
└── docs/                         CONTRIBUTING.md, runbooks, este informe
```
- **Las copias `azfunc\` y `bot_deploy_stage\` desaparecen como fuentes:** el deploy las genera desde `core/` + `apps/` (script o pipeline). Editar una copia deployada pasa a ser imposible por construcción.
- Diferencias legítimas entre entornos (backend de correo, paths) se resuelven por **configuración**, no por copias divergentes del código.

### 4.3 Datos: un solo dueño por estado
| Estado | Dueño único propuesto | Mecanismo |
|---|---|---|
| activity_state, reminders, conversation_refs, aad_lookup | **Bot (Azure)** | Corto plazo: JSON endurecido (atomic write `tmp`+`os.replace`, `threading.Lock` por archivo, backup `.bak`, cuarentena ante corrupción — `load()` NUNCA devuelve vacío silencioso: renombra el corrupto a `.corrupt-<ts>` y restaura el `.bak`). Mediano plazo: **SQLite en `/home`** (WAL) o Azure Table — transacciones reales, fin de los lost updates. |
| dispatch_state | **Azure Table** (ya existe) | El CLI local escribe a Table vía connection string o vía endpoint admin del bot. Eliminar el JSON local como fuente. |
| reply_state | **Azure Table** (ya existe) | Eliminar la versión local del reply agent (la tarea ya está disabled — borrar el wrapper para impedir re-enable). Adicional: marcar el correo como leído en Graph como segunda barrera anti-duplicado. |
| CLIs locales (`dispatch.py`, `activity_tracker.py`) | — | Pasan a ser clientes del estado remoto (HTTP al bot o Table directo), o se retiran si Teams ya los reemplazó. |

### 4.4 Identidad y autorización (fix del síntoma #1)
1. **Eliminar el paso 4 (display name) como fuente de autorización.** Si AAD override/cache/channel_data no resuelven: responder al usuario "no te reconozco, pide a Mateo que te registre" y NO crear state. El display-name match puede quedar solo como *sugerencia* en el mensaje de error.
2. `aad_lookup.json` se convierte en el registro canónico AAD→email, poblado explícitamente (endpoint admin ya existe), con backup.
3. Allowlists **fail-closed**: env var ausente = nadie entra (con log ERROR al arrancar).
4. `_resolve_collaborator` valida contra `KNOWN_COLLABORATORS` y rechaza emails arbitrarios.
5. Cards: embeber `user_email`, `fecha`, `wk` en el `data` del Action.Submit; al recibir, validar que coinciden con el contexto actual (si el card es de otra fecha/semana: pedir confirmación o registrar en la fecha del card).
6. Admin token propio (`ADMIN_API_TOKEN`), separado del secret OAuth, comparación constante (`hmac.compare_digest`).

### 4.5 Entrega confiable de reportes
1. **Ledger de envíos** (tabla/archivo transaccional): clave `(reporte, fecha)`, estados `pending/sent/failed`. Antes de enviar: si ya está `sent`, skip (idempotencia real). Después de enviar: marcar. Esto elimina duplicados por retry, por doble capa y por re-enable accidental.
2. **Catch-up al arrancar:** al iniciar el bot, revisar el ledger del día — si un reporte programado para hace <N horas no está `sent`, enviarlo ahora (resuelve el deploy a las 18:29).
3. **Todos los jobs con el mismo wrapper:** try/except + retry con backoff + alerta a Mateo si agotó reintentos (el patrón ya existe en `morning_sales_report` del bot — generalizarlo, no reinventarlo).
4. **Reportes degradados se marcan o no se envían:** si Contifico falló, el correo dice "⚠️ Datos de ventas no disponibles (error de Contifico)" en lugar de $0 — o no sale y alerta. Nunca cifras falsas con cara de reales.
5. **Heartbeat / dead-man's switch:** un job diario (o el propio consolidado) incluye "salud del sistema": últimos envíos OK/FAIL por reporte. Si el heartbeat no llega, Mateo sabe que el sistema entero está caído.
6. **Consolidar capas:** mover `logistics_morning` y `reply_agent_tick` al bot (como ya se hizo con el comercial) y **eliminar** las funciones del código azfunc (no solo deshabilitarlas por setting). Meta: una sola capa de scheduling (APScheduler) + el ledger como red de seguridad.
7. APScheduler: `misfire_grace_time=3600`, `coalesce=True` explícitos; guard de instancia única (lease en blob/Table con `WEBSITE_INSTANCE_ID`) antes de `scheduler.start()`.

### 4.6 Manejo de errores y observabilidad
- **Política "cero except silencioso":** todo `except` debe (a) loguear con `logger.exception` + contexto, y (b) o re-lanzar, o degradar VISIBLEMENTE (marca "⚠️ dato no disponible"), o alertar. Lintable con `ruff` (reglas `BLE001`, `S110` try-except-pass).
- Logging estructurado único (`logging` con formato JSON opcional) en vez de `print` — hoy conviven ambos.
- Los `.bat` restantes: reemplazar `wmic` por PowerShell (patrón ya correcto en `run_morning.bat:11`) y propagar `exit /b %errorlevel%`.

---

## 5. Estrategia de pruebas y CI/CD

**Suite de pruebas (pytest), en orden de valor:**
1. **Unit — capa de state** (la más crítica y la más testeable): atomic write sobrevive a kill simulado; load() ante archivo corrupto restaura backup y NO devuelve vacío; concurrencia (threads martillando mark_daily) no pierde escrituras; init_week idempotente; semanas ISO de 53 semanas; cierre de caja por sucursal.
2. **Unit — identidad:** matriz de resolución (AAD conocido, channel_data, display name ambiguo "Gabriela Bravo", sin AAD id) → asserts de que NUNCA dos usuarios distintos resuelven al mismo email y que los no-resueltos NO crean state. **Este test habría detectado el bug de producción.**
3. **Unit — contaminación de datos** (lo que pidió el usuario): test que crea actividades para N usuarios concurrentemente y verifica que cada actividad conserva usuario/fecha/semana/origen correctos. Property-based (hypothesis) si se quiere ir más lejos.
4. **Unit — cálculos de reportes:** meta/cumplimiento/días hábiles con feriados, fmt_pct/_as_ratio (casos borde documentados en R9), parsing de provincias (tabla de direcciones reales → provincia esperada), "ayer" en lunes.
5. **Integration — clientes con respuestas mockeadas** (httpx MockTransport): Contifico 500 → el reporte NO dice $0; Apollo 429 → el correo NO se marca procesado; Graph 401 → refresh+retry.
6. **Integration — scheduler:** ledger impide doble envío; catch-up tras "restart" simulado.
7. **E2E (smoke, manual o nightly):** `daily_report.py dry` y `daily_logistics_report.py dry` contra APIs reales en modo lectura, validando que el HTML se genera sin excepciones.

**CI/CD (GitHub Actions):**
- En cada PR: `ruff check` (incluye detección de except silenciosos) + `ruff format --check` + `mypy` (gradual, empezando por `core/state` y `core/identity`) + `pytest`.
- En merge a main: deploy automatizado que GENERA las copias de azfunc/bot desde core (fin del drift por construcción) + smoke test post-deploy (`/healthz` + un dry-run).
- Branch protection: no se mergea sin CI verde + 1 review.

---

## 6. Plan de refactorización por fases

Cada fase es independiente, desplegable y verificable. Orden diseñado para que el riesgo baje lo más rápido posible.

**Fase 0 — Fundación (1 día, sin tocar lógica):**
- `git init` + `.gitignore` (logs, caches, `.claude-agent`, tokens) + primer commit + repo GitHub privado.
- Arreglar los 4 `.bat` con `wmic` + propagación de exit codes.
- Borrar/archivar: `weekly_report.py` + su `.bat` (roto y superseded), `agent.py`, código muerto identificado.
- Eliminar del código azfunc los timers ya deshabilitados (`morning_sales_report`, `apollo_orchestrator_tick`) — que no puedan revivir.
- Actualizar CLAUDE.md a la realidad operativa verificada.

**Fase 1 — Blindar el state (2-3 días) → elimina P0 #1 y #4:**
- Módulo `core/state/safe_json.py`: atomic write + lock + backup + cuarentena. Migrar los 6 stores a ese módulo.
- Tests unitarios de la capa (suite #1 y #3 de §5).
- Deploy del bot y verificación con `/admin/state-debug`.

**Fase 2 — Identidad y autorización (2-3 días) → elimina P0 #2 y los riesgos C1/C5:**
- Quitar display-name del path de autorización; fail-closed; validación de colaboradores; contexto embebido en cards; admin token propio.
- Tests de identidad (suite #2).

**Fase 3 — Entrega confiable (3-4 días) → elimina P1 #7, #9, #11:**
- Ledger de envíos + wrapper universal de jobs con retry+alerta + catch-up + heartbeat.
- Reportes degradados visibles (R1, R3, C7): banner de error en vez de $0.
- Fix de C2/C3 en reply agent (error ≠ no-prospecto).
- Consolidar logistics y reply_agent al bot; vaciar azfunc.

**Fase 4 — Monorepo + CI (3-5 días) → elimina D1-D3 de forma permanente:**
- Reestructura a `core/` + `apps/`, deploy generado, GitHub Actions, branch protection.
- Resolver el drift: la copia raíz (más nueva en graph_mail/contifico) es la base; portar lo único que azfunc tiene mejor (Table Storage backends).

**Fase 5 — Calidad continua (incremental):**
- Externalizar config (destinatarios, feriados 2027+, overrides con año, umbrales) a `core/config`.
- Heurísticas con marca de confianza ("Sin identificar (revisar)" en vez de provincia adivinada).
- Partir `ask_agent.py` (tools / HTML builders / prompts) y deduplicar los 4 builders de tablas.
- TTL en `_DOCS_CACHE`, fix de timezone único (`America/Guayaquil` en todos lados), validación de inputs.

---

## 7. Buenas prácticas para el equipo (resumen para CONTRIBUTING.md)

1. **Todo cambio entra por PR con CI verde.** Nada se edita directo en producción ni en las copias de deploy.
2. **Un dato tiene UN dueño.** Antes de leer/escribir un state, pregunta: ¿qué runtime es el dueño? Si no eres el dueño, consume su API.
3. **Cero `except` silenciosos.** Si capturas, loguea con contexto y degrada visiblemente o alerta. "$0" no es un valor por defecto aceptable para un dato que falló.
4. **Nada de copias de archivos entre carpetas.** Código compartido vive en `core/`. Si te encuentras copiando un archivo, detente.
5. **Config en `core/config` o env vars, nunca inline.** Destinatarios, montos, fechas, umbrales.
6. **Todo job programado usa el wrapper estándar** (retry + alerta + ledger). No registres jobs "a pelo" en APScheduler.
7. **Fechas siempre con `America/Guayaquil` explícito.** `date.today()` está prohibido en código que corre en Azure.
8. **Identidad viene del registro AAD, no se adivina.** Usuarios no reconocidos se rechazan, no se inventan.
9. **Cambios de esquema de state requieren migración escrita + test**, no reparación in-place al vuelo.
10. **Si deshabilitas algo, bórralo del código** (o márcalo inerte por construcción). Un app setting no es documentación.

## 8. Plan de incorporación de nuevos desarrolladores sin generar inestabilidad

1. **Día 1:** lectura de CLAUDE.md (actualizado) + este informe + CONTRIBUTING.md. Acceso al repo con branch protection (no puede pushear a main).
2. **Entorno reproducible:** `requirements.txt` pineado + `pip install -e core/` + `pytest` local verde como rito de iniciación. Sin credenciales de producción al inicio: los tests corren con mocks.
3. **Primeras tareas en zonas de bajo riesgo** (apps/reports con dry-run, tests nuevos) — nunca en `core/state` ni `core/identity` el primer mes.
4. **CI como barrera mecánica:** lint + types + tests + review obligatorio hacen que la inexperiencia no llegue a producción.
5. **Deploy solo por pipeline:** un junior no puede (ni necesita) copiar archivos a azfunc o tocar el App Service a mano.
6. **Runbooks en `docs/`:** "qué hacer si no llegó el reporte de la mañana", "cómo registrar un colaborador nuevo", "cómo rotar un token" — el conocimiento sale de la cabeza de Mateo.

---

## Anexo: verificaciones de infraestructura realizadas (2026-06-12)

- Task Scheduler local: `ApolloNotifier-2hrs` READY; `DailyReport-Morning`, `LogisticsReport-Morning`, `ReplyAgent-15min` DISABLED; `WeeklyActivityReport-Friday` inexistente.
- `func-biodegradables-ec`: `AzureWebJobs.morning_sales_report.Disabled=true`, `AzureWebJobs.apollo_orchestrator_tick.Disabled=true` (verificado con `az`). `logistics_morning` y `reply_agent_tick` activos.
- `biodegradables-bot-app` (rg-biodegradables-prod): plan P0v3, capacity 1, `STATE_DIR=/home/.claude-agent` ✅ persistente.
- Drift por hash MD5: 8/13 módulos duplicados divergieron (graph_mail, contifico_client, daily_logistics_report, reply_agent, apollo_rest, outlook_client, dispatch_state, apollo_orchestrator).
- Logs reales: `apollo-notifier-*.log` recientes contienen solo el header de fecha (output de Python perdido — consistente con S7); `morning-*.log` muestran corridas manuales del 9-11/jun con encoding corrupto (UTF-16 con espacios).
- `~/.claude-agent` local: `activity_state.json` congelado desde 31/5 (confirma P2: el bot escribe en Azure, no aquí); `dispatch_state.json` = 2 bytes (`{}`, confirma P1).
