# PROPUESTA v3 — Marketing Brain: primer departamento de la empresa dirigida por IA (VER-IA)

**Fecha:** 2026-07-05 · **Estado:** propuesta para aprobación (diseño, sin código)
**Evolución del mandato:** v1 (automatizador de TikTok) → v2 (empleado virtual senior
de marketing orgánico) → **v3: el Marketing Brain es el primer departamento
completamente autónomo de una empresa dirigida por IA**, que a futuro convivirá con un
Departamento Comercial, Atención al Cliente, Financiero, RRHH y un Director General
(CEO Agent), colaborando entre sí mediante **contratos y eventos**, compartiendo solo
la información necesaria. TikTok pasa de ser "el proyecto" a ser el primer canal del
primer departamento. La visión organizacional está en §5; todo lo demás de v2
(conocimiento, autonomía, canales, producción) sigue vigente y se generaliza.

La investigación de campo de v1 (APIs de TikTok, publishers, riesgos, stack de
producción — verificada contra fuentes primarias el 2026-07-04) sigue vigente y está
resumida en §3 con su anexo de fuentes al final.

---

## 1. Tesis de diseño: qué cambia cuando el producto es un empleado

Un automatizador ejecuta tareas definidas por humanos ("publica 6 videos al día").
Un empleado senior recibe **objetivos y restricciones**, y decide él las tareas.
Esta diferencia no es filosófica — invierte la arquitectura:

| | Automatizador (v1) | Empleado (v2) |
|---|---|---|
| Unidad de entrada | Tarea ("6 videos/día") | Objetivo ("crecer alcance orgánico 20%/trimestre con presupuesto X") |
| El volumen/formato/canal | Input humano | **Output de la estrategia**, revisable por datos |
| El conocimiento | Implícito en prompts escritos a mano | **Activo de primera clase**: versionado, con procedencia, mantenido por el propio sistema |
| El aprendizaje | Bandit por marca | Por marca + **transferencia entre marcas** (el moat real a 100 tenants) |
| La autonomía | On/off | Escalera ganada por track record, con rendición de cuentas |
| Alcance | Publicar contenido | **Marketing orgánico completo**: contenido + comunidad + SEO/web, secuenciado |

**Las tres apuestas arquitectónicas de v2** (defendidas en §2):

1. **El moat es la capa de conocimiento, no la de automatización.** Publicar, renderizar
   y programar son commodities ($0–50/mes, cualquiera los compra). Lo que nadie puede
   copiar es un playbook por marca con cientos de experimentos documentados y una
   biblioteca de patrones cruzada entre marcas. Por eso el sistema de conocimiento se
   construye en la Fase 0, no como feature tardía.
2. **"Sin prompts manuales" se logra con contexto que el sistema escribe para sí mismo,
   no eliminando prompts.** El humano deja de escribir instrucciones porque el sistema
   mantiene su propio contexto (Brand Brain + playbooks). El humano aporta lo único que
   no es derivable: objetivos, restricciones y verdad de marca.
3. **Senior no significa "sin supervisión"; significa "sabe cuándo escalar".** La
   autonomía se diseña como protocolo de rendición de cuentas (OKRs, self-report,
   escalación), no como ausencia de controles.

---

## 2. Dónde esta propuesta difiere del pedido, y por qué

El mandato pide cuestionar. Cuatro desacuerdos técnicos, argumentados:

### 2.1 "Sin depender de prompts manuales" → contexto auto-mantenido
Eliminar prompts a secas produce un agente genérico que alucina la voz de la marca. La
solución correcta es que **el prompting sea un artefacto del sistema, no del humano**:
el Guionista no recibe un prompt escrito por Mateo; recibe el Brand Brain (§4.2) + el
playbook vigente + el plan semanal, todos generados y actualizados por otros agentes
del propio sistema. El humano interviene una vez (intake de marca) y luego solo cuando
el sistema escala una decisión. Esto es mejor que "cero prompts" porque es **auditable**:
puedes leer exactamente qué contexto produjo cada decisión.

### 2.2 "Cientos de marcas" → arquitectura multi-marca ya, construcción de flota después
Diseñar los límites correctos hoy es barato (todo estado keyed por `tenant_id`,
conocimiento separado en capa privada y capa compartida). Construir el control plane de
flota hoy sería especulación cara: no sabemos aún el costo real por marca, la tasa de
escalación ni el pricing. Disciplina de secuencia: **un empleado rentable y medible con
la marca #1, luego flota.** Es la misma decisión ya congelada en
`PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md` para la plataforma VER-IA — este módulo la hereda.

### 2.3 El pedido omite la mitad del marketing orgánico: la comunidad
El spec original solo lista *publicación*. Pero en orgánico, **responder comentarios y
convertir la conversación es de las palancas de mayor retorno** (señal de ranking + el
punto donde el alcance se vuelve cliente). Un "mejor sistema de marketing orgánico" sin
gestión de comunidad es un megáfono sin oídos. Se agrega como módulo (Fase 5), con la
honestidad de canal: en TikTok la API de comentarios para cuentas propias es limitada
(Business API, acceso restringido); en Instagram/Facebook es completa. El diseño lo
contempla; el rollout depende del canal. Mientras tanto: triage diario de comentarios →
borradores de respuesta → tarjeta Teams (mismo patrón del reply agent de correo que ya
opera en producción).

### 2.4 SEO/web es orgánico y ya tenemos el conector
La empresa ya tiene `wp_client.py` (WordPress, rol Editor, con `wp_apply.py` de cambios
controlados). El mismo empleado que aprende qué temas resuenan en TikTok debe
reutilizarlos como contenido SEO de blog (y viceversa: keywords SEO → ideas de video).
Canal barato de agregar (Fase 6) y multiplica el mismo contenido. El paid (ads) queda
explícitamente fuera de v2.

### 2.5 El CEO Agent no se construye primero: se construye el chasis
Un CEO Agent coordinando un solo departamento es un wrapper caro sin decisiones reales
que tomar: no hay presupuesto que arbitrar ni conflictos que resolver. Lo que SÍ debe
construirse desde el día 0 — porque retrofitearlo después es carísimo — son las
**primitivas organizacionales**: el chasis de departamento reutilizable, el formato de
contratos, el sobre de eventos y el directorio de departamentos (§5). El Marketing
Brain nace *siendo* un departamento (charter, presupuesto, contratos, eventos), no se
convierte en uno después. El CEO Agent llega en dos etapas (§5.4): "jefe de gabinete"
(consolida reportes y enruta escalaciones — barato, útil desde F7) y CEO real
(asignación de recursos entre departamentos — solo cuando existan ≥2 departamentos).
Construirlo antes sería teatro organizacional.

---

## 3. Restricciones de la realidad (investigación v1, resumen vigente)

Verificado contra fuentes primarias 2026-07-04 (anexo al final):

| Realidad | Consecuencia de diseño |
|---|---|
| TikTok Stories: **sin API** (ningún método) | Paso asistido: asset+caption listos, tarjeta Teams, 30 s manuales |
| Música del catálogo: **no seleccionable por API** (audio va embebido; `auto_add_music` solo en fotos) | Biblioteca licenciada propia + modo upload-to-inbox para trending (2 taps) |
| Apps no auditadas → posts privados; auditoría 4–7 semanas, rechaza "herramientas internas" | Publisher de terceros auditado ahora (Zernio gratis 1–2 cuentas / Buffer Free / Ayrshare $149); auditoría propia como SaaS VER-IA en paralelo (F4) |
| Browser automation: prohibido por ToS, VM anti-bot, ban risk | Descartado. Además no aporta: tampoco publica stories ni elige sonidos |
| Cap API ~15 posts/día; TikTok recomendaba 1–4/día; estudio Buffer 11.4M posts: el riesgo es la **repetitividad**, no el volumen | Volumen = parámetro decidido por el Estratega con datos; arranque 2 videos/día + 3 carruseles/semana |
| Display API expone solo views/likes/comments/shares (+followers). Sin watch-time/retención | El motor de aprendizaje optimiza sobre esas señales + follower delta |
| Creative Center sin API; Research API excluye empresas comerciales; scraping viola ToS | Tendencias vía Claude+web search (patrón `news_brief.py` ya probado) + señales propias + Google Trends |
| Carruseles (hasta 35 fotos) y video (≤4GB/10min) por API: ✅ | Producción propia: guion Claude → TTS Azure ($0, es-EC) → b-roll Pexels ($0) → render Remotion (gratis ≤3 personas) con subtítulos karaoke desde timestamps del TTS |

Nada de esto cambió con el reencuadre v2: son límites de plataforma, no de ambición.

---

## 4. Arquitectura v2

### 4.1 Vista de capas

```
┌─ CONTROL PLANE (flota) ─────────────────────────────────────────────┐
│ registro de tenants · salud/costos por marca · kill-switches ·      │
│ scorecards vs OKRs · escalaciones pendientes    (mínimo en F0,      │
│                                                  completo en F7)    │
├─ PROFESSION BRAIN (conocimiento COMPARTIDO entre marcas) ───────────┤
│ biblioteca de patrones: qué ganchos/formatos/horarios funcionan     │
│ por nicho e idioma — SOLO insights agregados y anonimizados,        │
│ nunca contenido ni datos de un tenant · priors de cold-start        │
├─ BRAND BRAIN (uno POR marca — contexto auto-mantenido) ─────────────┤
│ identidad: voz, catálogo, claims permitidos/prohibidos, ICP         │
│ objetivos: OKRs trimestrales (aprobados por humano)                 │
│ playbook: aprendizajes vigentes CON procedencia (experimento →      │
│   evidencia → confianza) — versionado, cada edición auditable       │
│ decision journal: qué decidió el sistema, por qué, con qué datos    │
│ experiment registry: hipótesis → variante → resultado → veredicto   │
├─ EMPLEADO (ciclo operativo por marca) ──────────────────────────────┤
│  PERCIBIR   métricas de posts · tendencias del nicho · comentarios  │
│  DECIDIR    Estratega (mensual) → Planificador (semanal/diario):    │
│             temas, formatos, volumen, horarios, experimentos        │
│  PRODUCIR   Guionista → Productor (determinista) → Gate de calidad  │
│  DISTRIBUIR Orquestador: scheduler + publish_ledger + retries       │
│  CONVERSAR  triage de comentarios → respuestas (borrador o auto)    │
│  APRENDER   Analista: score → actualiza playbook y experiment       │
│             registry → propone cambios de estrategia                │
│  RENDIR CUENTAS  self-report semanal vs OKRs a gerencia (Teams/mail)│
├─ CANALES (adapters intercambiables) ────────────────────────────────┤
│ TikTok (F2) · WordPress/SEO (F6) · Instagram (F7) · FB/YT/X (luego) │
│ contratos: Publisher · MetricsSource · CommunityInbox ·             │
│ PlatformProfile (YAML declarativo por red)                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Brand Brain: el contrato humano↔empleado

Única intervención humana estructural (onboarding, ~1 hora por marca):

- **Verdad de marca** — qué es la empresa, catálogo, diferenciadores, qué claims puede
  hacer y cuáles jamás (regulatorio), tono. Para el tenant #1 ya existe:
  `company_context.md` es el embrión del Brand Brain.
- **Objetivos (OKRs trimestrales)** — p. ej. "seguidores 0→5k", "≥3 leads/semana desde
  orgánico", "CPM orgánico equivalente < $X". Los aprueba gerencia; el empleado no
  puede editarlos, solo proponer revisiones.
- **Restricciones duras** — presupuesto mensual (USD y posts/día máx), ventanas
  horarias, temas vetados, nivel de autonomía vigente.

Todo lo demás del Brand Brain (playbook, journal, experimentos) **lo escribe y mantiene
el sistema**. Regla de oro: *ningún agente actúa con contexto que no esté escrito en el
Brand Brain* — es lo que hace al empleado auditable, transferible entre modelos LLM, y
lo que elimina los prompts manuales sin perder control.

### 4.3 El ciclo de aprendizaje (el corazón del sistema)

1. **Score por post** = f(views, likes, comments, shares, follower_delta) normalizado
   por hora y edad del post.
2. **Todo post es un experimento etiquetado**: pilar temático, tipo de gancho, formato,
   duración, franja, CTA, densidad de hashtags. Sin etiqueta no se publica (si no
   puedes atribuir el resultado, no aprendes).
3. **Asignación epsilon-greedy**: ~80% explota el playbook, ~20% explora hipótesis
   nuevas propuestas por el Analista.
4. **Actualización del playbook con procedencia**: cada regla del playbook lleva
   `evidencia: [exp_ids]`, `confianza: baja|media|alta`, `vigencia`. El Analista
   promueve, degrada o retira reglas — nunca las sobreescribe sin registrar por qué.
   Un humano puede leer el playbook y entender al empleado en 10 minutos.
5. **Destilación a Profession Brain** (F7): reglas con confianza alta se generalizan
   como priors por nicho ("food-service LATAM: tutoriales <35 s, gancho de pregunta"),
   **sin contenido ni métricas identificables del tenant**. Marca nueva del mismo nicho
   arranca con esos priors en vez de en frío. Política de conflicto: dos marcas
   competidoras en el mismo nicho/ciudad no comparten aprendizajes de nicho entre sí.

### 4.4 Escalera de autonomía y rendición de cuentas

| Nivel | Qué decide solo | Qué escala | Cómo se gana |
|---|---|---|---|
| **L0 supervisado** (semanas 1–4) | Nada publica sin aprobación (tarjeta Teams: aprobar/regenerar/descartar) | Todo | — |
| **L1 semi-autónomo** | Publica lo que el Gate puntúa ≥ umbral | Contenido dudoso, cambios de estrategia | 2 semanas en L0 con <10% rechazo humano |
| **L2 autónomo operativo** | Todo el ciclo de contenido y comunidad (respuestas dentro de política) | Cambios de OKRs, temas nuevos sensibles, presupuesto | 2 semanas en L1 sin rechazos de auto-aprobados |
| **L3 autónomo estratégico** | Además: re-balancear mix de canales y volumen dentro del presupuesto | Solo OKRs y restricciones duras | 1 trimestre en L2 cumpliendo OKRs |

**Rendición de cuentas (siempre, en todo nivel):** self-report semanal a gerencia
(resultados vs OKRs, decisiones tomadas y por qué, experimentos, siguiente semana,
riesgos) — mismo canal `graph_mail`/Teams ya en producción. Protocolo de escalación:
incertidumbre alta o regla dura rozada → pregunta puntual por Teams, con default seguro
si nadie responde en 24 h (no publicar). Kill-switch por marca: `/marketing pause`.

**Reglas duras jamás editables por LLM:** presupuesto, claims prohibidos, ventanas
horarias, política de respuesta a comentarios (nunca discutir, nunca prometer precios
sin fuente), credenciales, `publish_ledger`.

### 4.5 Datos e infraestructura

- **Estado:** SQLite WAL por tenant (F0) → Azure PostgreSQL con `tenant_id` (F7).
  Tablas: `ideas`, `content_packages`, `renditions`, `schedule_slots`,
  `publish_ledger`, `metrics_snapshots`, `experiments`, `playbook_rules`,
  `decision_journal`, `community_items`, `system_events`.
- **Conocimiento legible:** playbook y journal también se materializan como Markdown
  versionado en git por tenant (`tenants/<t>/marketing/`) — el conocimiento del
  empleado se lee como documentación, que es exactamente lo pedido ("documente su
  conocimiento").
- **Cómputo:** worker en PC de Mateo (F1–F2, SPOF conocido y aceptado como en Apollo
  notifier) → Azure Container Apps Job para render Remotion (Node+Chrome) + App Service
  existente para orquestación ligera (F3+). Assets en Blob Storage.
- **Reutilización probada del repo:** `safe_json`, patrón `send_ledger`,
  `_reliable_job`/APScheduler, bot Teams + Adaptive Cards, `graph_mail`,
  patrón Claude+web search de `news_brief.py`, `wp_client.py`, config por tenant YAML.
- **Dashboard** (FastAPI + HTML server-rendered, patrón admin del bot): hoy/cola,
  calendario, galería de contenido, métricas por dimensión, **playbook e historial de
  decisiones** ("por qué hiciste esto" respondible con un click), experimentos, salud,
  costos por marca, escalaciones pendientes.

### 4.6 Producción (sin cambios de fondo vs v1)

Guion (Claude Sonnet, JSON validado) → TTS Azure neural es-EC/es-MX ($0, free tier;
A/B contra ElevenLabs $22) → b-roll Pexels/Pixabay ($0) → subtítulos karaoke con
timestamps del propio TTS → render **Remotion** 1080×1920 (gratis ≤3 personas; plan B:
JSON2Video $50/mes) → portada → QA técnico + revisor Claude con rúbrica de marca.
Carruseles: plantillas HTML → screenshot Playwright local → photo post API.
Historia diaria: asistida por tarjeta Teams (límite de plataforma, §3).

---

## 5. La empresa autónoma: el Marketing Brain como departamento #1

### 5.1 El chasis de departamento (Department Kernel)

La observación clave: **todo lo que v2 definió para el "empleado" no es específico de
marketing.** Charter con OKRs y presupuesto, contexto auto-mantenido, playbook con
procedencia, decision journal, escalera de autonomía L0→L3, reglas duras, self-report,
protocolo de escalación — eso es el esqueleto de *cualquier* departamento autónomo.
v3 lo extrae a un chasis reutilizable (`org/kernel`):

```
Departamento = KERNEL (genérico)              + DOMINIO (específico)
               charter: misión, OKRs,           agentes propios (Estratega,
                 presupuesto, límites de          Guionista, Analista…)
                 autoridad                      puertos propios (Publisher,
               brain: contexto + playbook         MetricsSource…)
                 con procedencia                contratos que ofrece y
               decision journal                   consume (§5.2)
               autonomía L0→L3 + escalación     conocimiento de dominio
               self-report + scorecard OKRs
               reglas duras (no editables)
               identidad: principal con
                 credenciales de mínimo
                 privilegio
```

Costo de construir el kernel como capa separada en F0: ~2–3 días sobre el plan v2
(las piezas se iban a construir igual; solo cambia dónde viven). Beneficio: el
Departamento Comercial futuro se construye escribiendo SOLO su columna derecha.

### 5.2 Contratos entre departamentos

Registro versionado de esquemas (`org/contracts/*.json`, JSON Schema + semver).
Principios innegociables:

1. **Mínimo necesario:** los payloads llevan IDs y agregados, nunca datos crudos del
   dominio ajeno (Marketing recibe "el lead #123 cerró por $450", no el historial
   completo del cliente).
2. **Cada departamento es dueño de sus datos.** Acceso SOLO vía contrato — ningún
   departamento lee la base de otro. (Regla de lint en CI: `marketing/` no importa de
   `comercial/` salvo `org/contracts/`.)
3. **Compatibilidad:** cambios breaking = versión nueva del contrato; ambas conviven
   durante la migración.

Contratos fundacionales (los dos primeros cierran el loop de atribución de Marketing —
son la razón económica de todo esto):

| Contrato | Dirección | Contenido mínimo |
|---|---|---|
| `LeadHandoff` | Marketing → Comercial | lead_id, canal/post de origen, contexto de intención |
| `LeadOutcome` | Comercial → Marketing | lead_id, resultado (ganado/perdido), valor — **convierte el playbook de vanity metrics a revenue real** |
| `VoiceOfCustomer` | Atención → Marketing | temas/quejas frecuentes agregados y anonimizados → ideas de contenido |
| `BudgetEnvelope` | Financiero → depto | presupuesto del periodo, límites |
| `SpendReport` | depto → Financiero | gasto real por categoría |
| `OKRDirective` / `WeeklyDeptReport` / `EscalationRequest` | CEO ↔ depto | objetivos, resultados vs OKRs, decisiones que exceden autoridad |

### 5.3 Eventos

Bus de eventos **append-only por tenant** con sobre estándar:
`{event_id, tenant_id, dept, type, schema_version, occurred_at, correlation_id, payload}`.
Pub/sub por tópicos; consumidores idempotentes por `event_id` (mismo patrón
`send_ledger` ya probado). **Implementación F0: una tabla `org_events` en SQLite**
(→ Azure Storage Queue/Service Bus cuando haya ≥2 departamentos). No Kafka, no broker:
la semántica correcta hoy, la infraestructura pesada cuando el volumen la pida.
Marketing emite desde el día 1 (`content.published`, `lead.captured`,
`report.weekly_ready`, `escalation.raised`) aunque el único consumidor inicial sea el
dashboard — el historial de eventos ES la memoria organizacional del futuro CEO.

### 5.4 CEO Agent: dos etapas, board humano siempre

- **Etapa 1 — Jefe de gabinete (F7):** consolida los self-reports de departamentos en
  un reporte único a gerencia (el patrón `consolidated_daily_summary` ya existe en
  producción), enruta escalaciones al humano correcto, detecta OKRs en conflicto.
  No decide: informa y enruta. Costo marginal ~$5–10/mes de LLM.
- **Etapa 2 — CEO real (cuando existan ≥2 departamentos):** propone asignación de
  presupuesto y OKRs entre departamentos (sube al board para aprobación), arbitra
  conflictos inter-departamento por contrato, sube su propia escalera de autonomía
  igual que cualquier departamento.
- **El board es humano, siempre:** gerencia define misión y presupuesto global,
  aprueba OKRs, puede vetar o pausar cualquier departamento (`/org pause <dept>`).
  Una "empresa dirigida por IA" sin board humano no es un objetivo de diseño de VER-IA:
  es un pasivo legal.

### 5.5 La empresa ya existe en embrión (mapa de migración)

Los otros departamentos no se construirán de cero: las automatizaciones actuales de la
plataforma son sus embriones. Cuando toque, se envuelven en el kernel (charter + brain
+ contratos) en vez de reescribirse:

| Departamento futuro | Embrión ya en producción |
|---|---|
| Comercial | Reporte diario de ventas, forecasting, Apollo/reply agent, pipeline HubSpot |
| Atención al Cliente | Data Bot / Activities Bot Teams, agente WhatsApp (diseñado), chatbots web |
| Financiero | Cobranzas auto-asignadas, cierre de caja, recap mensual, cartera vencida |
| RRHH / Operaciones | Activity tracker, check-ins diarios, weekly summaries, consolidado 18:30 |
| **Marketing** | **Este proyecto — el primero que nace con el kernel completo** |

### 5.6 Seguridad organizacional

Cada departamento es un **principal** con credenciales propias de mínimo privilegio
(Marketing tiene el token del publisher y NADA de Contifico; Comercial al revés).
Jerarquía de reglas duras: corporativas (board) > departamentales > preferencias del
agente — las de arriba no son editables por nada de abajo. Los decision journals de
todos los departamentos son legibles por el CEO Agent y el board (auditoría cruzada),
pero los datos de dominio no. Y la frontera más dura sigue siendo el **tenant**: los
departamentos colaboran dentro de una empresa; jamás cruzan datos entre empresas.

---

## 6. Economía por marca (la métrica que decide la escala)

El control plane trackea desde F0 el **costo total por marca/mes** (LLM + render +
publisher + infra prorrateada) contra el valor generado (alcance, leads atribuibles).

| Concepto | Marca #1 (arranque) | En flota (≥10 marcas) |
|---|---|---|
| LLM (estrategia+guiones+análisis+comunidad) | $20–50 | $15–35 (priors reducen retrabajo) |
| TTS + b-roll + render | $0–5 | $2–8 (Container Apps) |
| Publisher | $0 (Zernio 1–2 ctas / Buffer Free) | ~$1–6/marca (Zernio por volumen) o $0 con app propia auditada |
| Infra prorrateada | $5–15 | $3–10 |
| **Total/marca/mes** | **~$30–70** | **~$25–60** |

Con VER-IA cobrando el módulo a $150–400/marca/mes, el margen es 70–85%. **Gate de
escala:** no se onboardea la marca #2 hasta que la #1 tenga ≥1 trimestre de OKRs
cumplidos y costo/marca estable — el negocio escala sobre evidencia, no sobre promesa.

---

## 7. Plan por fases (revisado v3)

| Fase | Alcance | Criterio de salida | Est. |
|---|---|---|---|
| **F0 — Fundaciones + kernel org + Brand Brain** | `org/kernel` (charter, journal, autonomía, self-report genéricos) + `org/contracts` + tabla `org_events`; módulo `marketing/` como primer departamento sobre el kernel; modelos de datos (playbook/journal/experiments **desde el día 0**), puertos, Brand Brain del tenant #1 (migrando `company_context.md`), charter y OKRs aprobados por gerencia | Esquema estable, kernel separado del dominio, charter firmado, CI verde | 1.5–2 sem |
| **F1 — Producción** | Pipeline guion→TTS→Remotion→QA + carruseles. 10 piezas demo | Calidad aprobada por gerencia | 2 sem |
| **F2 — Publicación L0** | Adapter Zernio + ledger + scheduler + aprobación Teams. Posts públicos reales, cada post etiquetado como experimento | 1 semana a 2 videos/día sin incidentes | 1–2 sem |
| **F3 — Ciclo de aprendizaje** | Ingesta métricas, scoring, Analista, primera actualización de playbook con procedencia, decision journal, tarjeta diaria de historia | Primer ciclo completo métrica→regla→post mejorado | 2 sem |
| **F4 — Autonomía L1 + dashboard + self-report** | Gate auto-aprobación, dashboard completo, reporte semanal a gerencia, kill-switch. Solicitud de auditoría TikTok propia (como SaaS VER-IA) en paralelo | L1 activo, primer self-report enviado | 2 sem |
| **F5 — Comunidad** | Triage de comentarios + borradores de respuesta (asistido primero, auto dentro de política después) | Respuesta <24h sostenida 2 semanas | 1–2 sem |
| **F6 — Canal SEO/web** | Reuso de contenido ganador → posts de blog vía `wp_client`/`wp_apply` (flujo dry-run→approve existente) | 4 posts SEO publicados derivados del playbook | 1–2 sem |
| **F7 — L2/L3 + flota + CEO etapa 1** | Autonomía L2, volumen por datos, migración render a Azure, Profession Brain (destilación con aislamiento), control plane multi-marca, onboarding de marca #2 (posible = Andex, tenant demo), **CEO Agent "jefe de gabinete"** (reporte consolidado + ruteo de escalaciones) | Marca #2 operando con priors, costo/marca medido, primer reporte consolidado org | 3 sem |

**~14–16 semanas** hasta departamento L2 multi-canal con flota iniciada y CEO etapa 1;
valor visible desde la semana 3–4 (F1) y publicación real desde la semana 5–6 (F2).
El costo del paso v2→v3 es ~1 semana extra total (kernel en F0 + CEO etapa 1 en F7):
barato, porque compra la opción de construir cada departamento futuro solo con su
lógica de dominio.

---

## 8. Riesgos principales

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Contenido repetitivo → demonización algorítmica | Media | Alto | Dedup semántico, rotación de pilares, regla anti-near-duplicate en QA, exploración forzada 20% |
| Error de marca/claim publicado | Baja | Alto | Claims duros en Brand Brain + Gate + L0/L1 inicial |
| Respuesta de comunidad inapropiada | Media | Alto | F5 arranca asistido; política dura de respuesta; auto solo dentro de whitelist de intenciones |
| Zernio (startup) muere/cambia pricing | Media | Medio | Puerto Publisher (swap a Buffer/Ayrshare en horas); auditoría propia desde F4 |
| Rechazo de auditoría propia | Media | Bajo | Seguimos en terceros; re-aplicar como SaaS con tracción |
| Playbook aprende ruido (falsos patrones) | Media | Medio | Confianza por evidencia mínima (n≥5 experimentos), decay temporal, revisión del Analista con test estadístico simple |
| PC de Mateo apagada (F1–F2) | Alta | Medio | Cola persistente + reprogramación; migración Azure en F7 (o antes si duele) |
| Cambio de APIs de canal | Media | Medio | Todo lo específico vive en 1 adapter + tests de contrato |
| Costo LLM se dispara con volumen | Baja | Medio | Budget por marca en reglas duras; telemetría de costo por pieza en dashboard |

---

## 9. Decisiones que necesito de gerencia antes de F0

1. **OKRs del trimestre para la marca #1** (propuesta inicial: 0→3k seguidores TikTok,
   ≥2 leads/semana atribuibles a orgánico, ≥12 posts/semana sostenidos) — son el
   contrato del empleado; sin OKRs no hay empleado, hay generador de contenido.
2. **Volumen de arranque:** ratifico 2 videos/día + 3 carruseles/semana con escalado
   por datos (el "6/día" pasa a ser decisión del Estratega cuando la evidencia lo
   soporte). ¿De acuerdo?
3. **Historia diaria asistida** (30 s/día manuales) como única opción honesta: ¿aceptado?
4. **Publisher:** Zernio gratis vs Ayrshare $149/mes por madurez. Recomiendo Zernio +
   fallback Buffer, dado el puerto intercambiable.
5. **Voz:** Azure $0 (es-EC disponible) con A/B ElevenLabs, ¿o directo ElevenLabs $22?
6. **Cuenta TikTok:** ¿prueba 2 semanas y luego @biodegradablesecuador, o directo la real?
7. **Comunidad (F5):** ¿autorizan respuestas automáticas dentro de política tras el
   periodo asistido, o comunidad queda asistida permanentemente?
8. **Pilares de contenido** (3–5) para el Brand Brain inicial — propuesta: educación
   sostenibilidad, producto en uso real, tips food-service, detrás de cámaras,
   tendencias eco Ecuador.
9. **Secuencia organizacional:** ¿ratifican kernel + contratos + eventos en F0, CEO
   Agent etapa 1 (jefe de gabinete) en F7, y CEO etapa 2 solo cuando exista el segundo
   departamento? (Es mi recomendación fuerte, ver §2.5.)
10. **Segundo departamento:** recomiendo **Comercial** — sus embriones ya operan
    (reporte de ventas, Apollo, HubSpot) y su contrato `LeadOutcome` es el que convierte
    el aprendizaje de Marketing de métricas de vanidad a revenue real. ¿De acuerdo, o
    prefieren Atención al Cliente?

---

## Anexo — Fuentes principales (verificadas 2026-07-04)

**Oficiales TikTok:** developers.tiktok.com/doc/content-posting-api-reference-direct-post ·
/doc/content-sharing-guidelines · /doc/content-posting-api-reference-photo-post ·
/doc/content-posting-api-media-transfer-guide · /doc/display-api-get-started ·
/doc/tiktok-api-v2-video-object · /doc/research-api-faq ·
/doc/commercial-content-api-getting-started · tiktok.com/community-guidelines/en/integrity-authenticity ·
tiktok.com/legal/page/us/terms-of-service (act. 2026-01-22)

**Publishers:** ayrshare.com/pricing y docs TikTok · zernio.com/pricing (ex getlate.dev) ·
buffer.com/developer-api · blotato.com/pricing · metricool.com/pricing ·
docs.mixpost.app/services/social/tik-tok/direct-post-audit (proceso real de auditoría)

**Riesgo/frecuencia:** buffer.com/resources/how-often-should-you-post-on-tiktok (estudio 11.4M posts, oct-2025) ·
blog.castle.io/what-tiktoks-virtual-machine-tells-us-about-modern-bot-defenses (jun-2025) ·
github.com/wkaisertexas/tiktok-uploader (issues 2025–2026) · newsroom.tiktok.com (transparency reports)

**Producción:** remotion.pro/license · remotion.dev/docs/captions/create-tiktok-style-captions ·
github.com/remotion-dev/template-tiktok · github.com/harry0703/MoneyPrinterTurbo (v1.3.0 jun-2026) ·
github.com/m-bain/whisperX · azure.microsoft.com/pricing/details/speech · elevenlabs.io/pricing ·
json2video.com/pricing · shotstack.io/pricing · pexels.com/api
