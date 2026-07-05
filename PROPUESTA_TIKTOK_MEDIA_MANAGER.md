# PROPUESTA — AI Media Manager para TikTok (multi-red a futuro)

**Fecha:** 2026-07-04 · **Estado:** propuesta para aprobación (fase de diseño, sin código)
**Contexto:** plataforma VER-IA — este sistema nace como módulo nuevo, diseñado multi-tenant y multi-plataforma desde el día 1. Biodegradables Ecuador es el tenant #1.

Toda afirmación sobre TikTok en este documento fue verificada contra fuentes primarias
(docs oficiales de developers.tiktok.com y pricing pages, consultadas 2026-07-04).
Las URLs están en el anexo de fuentes al final.

---

## 1. Resumen ejecutivo

Se puede construir un AI Media Manager autónomo de nivel profesional, pero **tres de los
requisitos pedidos son imposibles hoy vía cualquier método seguro**, y uno más es
contraproducente. Este documento propone la arquitectura completa, con esos límites
declarados y con la alternativa concreta para cada uno:

| Requisito pedido | Veredicto | Alternativa propuesta |
|---|---|---|
| 6 videos/día | ⚠️ Posible pero subóptimo | Empezar con 2/día y escalar por datos (§3.4) |
| 1 historia/día | ❌ Imposible por API | No existe endpoint de Stories. Queda como paso manual asistido (§3.2) |
| Música trending automática | ❌ Imposible por API | El audio va embebido en el video; biblioteca propia licenciada + modo semi-manual para trending (§3.3) |
| Carruseles automáticos | ✅ Viable | Photo posts hasta 35 imágenes vía API |
| Publicación autónoma | ✅ Viable | Vía servicio auditado de terceros (§4) |
| Métricas y aprendizaje | ⚠️ Parcial | views/likes/comments/shares sí; **retención/watch-time no** está expuesto por API (§3.5) |
| Autonomía total sin humano | ⚠️ Viable pero no recomendada día 1 | Autonomía progresiva con gate de aprobación por Teams las primeras semanas (§6.4) |
| Multi-red futura | ✅ Viable | Arquitectura de puertos y adaptadores (§5) |

**Costo estimado del escenario recomendado: ~$30–80/mes** (dominado por Claude API).
Escenario premium con voz ElevenLabs y publisher Ayrshare: ~$200–250/mes. Detalle en §8.

---

## 2. Hallazgos de la investigación (lo que la realidad permite)

### 2.1 API oficial de TikTok (Content Posting API)

- **Dos modos:** *Direct Post* (publica directo al perfil) y *Upload-to-inbox* (el video
  llega al inbox del creador, que termina el post manualmente en la app).
- **Apps no auditadas: todo post sale privado** (`SELF_ONLY`, error explícito
  `unaudited_client_can_only_post_to_private_accounts`) y máximo 5 usuarios/24h.
  La auditoría de Direct Post exige app con apariencia de producto real (landing,
  privacy policy, mockups UX, video demo), tarda 4–7 semanas en ciclos reales de 2026 y
  **rechaza lo que parece herramienta interna**.
- **Límites (audited):** ~15 posts/día por cuenta creadora (compartido entre todas las
  apps), 6 requests/minuto por token. Videos MP4/H.264 hasta 4 GB / 10 min.
  Carruseles: hasta 35 fotos, `photo_cover_index` para la portada.
- **No existe:** scheduling nativo (el scheduler es responsabilidad nuestra), selección
  de sonido del catálogo (solo `auto_add_music` en fotos: música recomendada sin elegir),
  Stories, duets/stitches, edición post-publicación.
- **Research API / Commercial Content API:** solo investigadores académicos de
  EE.UU./Europa. Una empresa comercial ecuatoriana **no califica**. Descartadas.
- **Creative Center** (tendencias de hashtags/sonidos): web pública **sin API oficial**;
  scrapearlo viola ToS (endurecido en abril 2026) y arriesga el API client.

### 2.2 Rutas de publicación evaluadas

| Ruta | Costo | Público directo | Carrusel | Story | Música catálogo | Riesgo | Esfuerzo |
|---|---|---|---|---|---|---|---|
| App propia sin auditar | $0 | ❌ (privado) | ✅ privado | ❌ | ❌ | Nulo | Medio |
| App propia auditada | $0 + 4–7 sem. auditoría | ✅ | ✅ | ❌ | ❌ | Rechazo de auditoría | Alto |
| **Terceros auditados (API)** | $0–149/mes | ✅ | ✅ | ❌ | ❌ (auto_add_music en fotos) | Bajo | **Bajo** |
| OSS self-hosted (Postiz/Mixpost) | $0 + VPS | ⚠️ requiere **tu propia app auditada** — no elimina la auditoría | | ❌ | ❌ | Igual que app propia | Alto |
| Browser automation (Playwright) | $0 | ✅ hasta que falle | ✅ | ❌ | ❌ (solo audio recomendado) | **Prohibido por ToS, ban/shadowban, mantenimiento perpetuo** | Alto |

Terceros verificados (todos montados sobre la API oficial auditada de ellos, por lo que
heredan los mismos límites de TikTok):

- **Ayrshare** — $149/mes (1 perfil). El más maduro y documentado, SDK Python oficial,
  15 videos/día por usuario, fotos hasta 35 imágenes, `autoAddMusic`, analytics API.
- **Zernio (ex Late/getlate.dev)** — 1–2 cuentas **gratis**, luego $6/cuenta/mes.
  API REST completa + analytics + webhooks. Muy joven (rebrand reciente = riesgo de churn).
- **Buffer** — API GraphQL incluida **en el plan Free** (100 posts/24h). Empresa
  establecida (2010), partner auditado de TikTok. Analytics vía API limitado.
- **Blotato** — desde $29/mes (hasta 900 posts TikTok/mes), popular en n8n/Make;
  producto joven, gating del API por tier no documentado con claridad.
- Metricool/Publer: API secundaria en tiers medios ($21–53/mes). SocialBee: sin API pública.

**Browser automation, descartado con evidencia:** TikTok corre una VM JavaScript
anti-bot (webmssdk) con fingerprinting y CAPTCHAs propios; el repo líder
(`wkaisertexas/tiktok-uploader`) vive rompiéndose con cada cambio de TikTok Studio
(issues de "Something went wrong" durante todo 2025–2026); las Community Guidelines
prohíben explícitamente "automation tools, scripts, or other tricks designed to bypass
its systems" con enforcement hasta ban de cuenta. Para la cuenta real de una empresa,
inaceptable. Además **no aporta nada**: tampoco puede publicar Stories ni elegir sonido
trending (eso es solo app móvil).

### 2.3 Riesgo de frecuencia (¿6 al día?)

- El cap técnico por API es ~15/día → 6/día es legal.
- La guía histórica del propio TikTok: **1–4 posts/día**.
- Buffer (estudio oct-2025, 11.4M posts): publicar más NO penaliza el alcance por post
  (6–10 posts/semana = +29% views/post vs 1/semana), pero el óptimo esfuerzo/retorno
  está en 2–5/semana; el riesgo real no es el número sino **la uniformidad del
  contenido** (duplicados/near-duplicates y watermarks se demonizan desde sep-2025).
- Producir 6 videos/día *buenos* y *distintos* es el verdadero cuello de botella. 6
  mediocres al día rinden menos que 2 excelentes, y entrenan al algoritmo en contra.

**Recomendación firme:** arrancar con 2 videos/día + 3 carruseles/semana, medir 4
semanas, y que el propio agente estratega proponga subir el volumen cuando los datos lo
justifiquen. La arquitectura soporta 6/día desde el día 1 (es un parámetro de config);
el número inicial es una decisión de estrategia, no una limitación técnica.

### 2.4 Métricas disponibles

Display API (scopes `user.info.basic` + `video.list`): **views, likes, comments,
shares** por video + metadata. **NO expone** watch-time, retención, completion rate,
demografía ni saves — eso vive solo en TikTok Analytics in-app (o Business API con
acceso de partner, barrera alta). El motor de aprendizaje se diseña sobre las 4 métricas
disponibles + follower delta, que es suficiente para optimizar tema/gancho/formato/horario.

### 2.5 Detección de tendencias sin violar ToS

- ❌ Scraping de Creative Center conectado al pipeline: descartado (ToS).
- ✅ **Claude API con web search** (patrón ya probado en `news_brief.py` del repo):
  brief diario de tendencias del nicho (sostenibilidad, empaques, PYMEs Ecuador) desde
  fuentes web abiertas.
- ✅ **Señales propias**: qué contenido nuestro y de cuentas comparables rinde
  (Display API propia + revisión manual asistida semanal).
- ✅ Google Trends (pytrends) para estacionalidad de temas.
- ⚠️ Apify scrapers de TikTok: existen y funcionan (~$0.50–5/1000 items) pero
  jurídicamente grises; solo si el usuario acepta el riesgo explícitamente, y nunca
  ligados a las credenciales del publisher.

---

## 3. Decisiones de diseño derivadas (con sus porqués)

### 3.1 Publicación: adaptador de terceros primero, app propia después
**Fase 1–3: Zernio (gratis, 1–2 cuentas) con fallback documentado a Buffer Free.**
Cero costo, cero auditoría, publicación pública real desde la semana 1. Se encapsula
tras un puerto `Publisher` (interfaz nuestra), de modo que migrar a Ayrshare ($149) o a
una app propia auditada (cuando VER-IA presente su SaaS multi-tenant a la auditoría de
TikTok — mucho más aprobable que una herramienta interna) sea cambiar un adaptador, no
el sistema. La solicitud de auditoría propia se lanza en paralelo en Fase 4: es la única
ruta sin dependencia de terceros y con costo marginal $0 para los 50 tenants futuros.

### 3.2 Stories: paso manual asistido, honesto
No hay API. El sistema genera el asset de la historia (imagen/video 9:16) + caption, lo
deja en la cola con todo listo, y envía **una tarjeta diaria por Teams** (infra que ya
existe) con el archivo y el texto para publicarla desde el teléfono en ~30 segundos.
Si TikTok abre API de Stories, se enchufa al mismo puerto. No se promete autonomía
donde no puede existir.

### 3.3 Música: biblioteca propia + trending semi-manual
- **Ruta autónoma (default):** música de biblioteca licenciada libre de derechos,
  embebida en el render. Curada por nicho/mood en un catálogo propio versionado.
- **Ruta trending (opcional, 1–2 videos/semana):** el video se envía en modo
  *upload-to-inbox*; Mateo lo abre en la app, elige el sonido trending sugerido y
  publica (2 taps). Es el único método legítimo que existe para sonido de catálogo.
- En carruseles: `auto_add_music=true` (TikTok pone música recomendada solo).

### 3.4 Volumen: parámetro, no promesa
`posts_per_day` vive en la config del tenant. Arranque: 2 videos + historia asistida;
carruseles cuando el planificador decida que el contenido es enumerable/educativo
(regla: listas, comparativas, tips → carrusel; narrativa/demostración → video).

### 3.5 Aprendizaje: bandit sencillo sobre 4 métricas, no ML especulativo
Score por post = f(views, likes, comments, shares, follower_delta) normalizado por hora
de publicación y edad. Dimensiones aprendibles: pilar temático, tipo de gancho, formato,
duración, franja horaria, estilo de CTA, densidad de hashtags. Selección
epsilon-greedy: ~80% explota lo que funciona, ~20% explora. Todo trazable en el
dashboard ("por qué el sistema decidió esto").

---

## 4. Arquitectura general

Principio rector: **el core no sabe qué es TikTok.** Igual que la plataforma VER-IA
(núcleo + módulos + conectores por tenant), este sistema es un módulo `media_manager`
con conectores por red social.

```
┌────────────────────────── CORE (agnóstico de plataforma) ──────────────────────────┐
│                                                                                     │
│  ESTRATEGA (semanal, Claude)     PLANIFICADOR (diario, Claude)                      │
│  pilares de contenido, mix       elige temas del backlog, formato por contenido,    │
│  de formatos, objetivos,         horarios (evita repetición), encola ContentPlan    │
│  lecciones del ANALISTA               │                                             │
│         ▲                             ▼                                             │
│  ANALISTA (diario, Claude+stats) GUIONISTA (por ítem, Claude)                       │
│  ingesta métricas, score,        guion + hook + título + caption + CTA + hashtags   │
│  actualiza "playbook" de         + brief visual + selección de música de biblioteca │
│  aprendizajes                         │                                             │
│         ▲                             ▼                                             │
│         │                        PRODUCTOR (determinista, sin LLM)                  │
│         │                        TTS → b-roll/imágenes → subtítulos karaoke →       │
│         │                        render 9:16 + portada + QA técnico automático      │
│         │                             │                                             │
│         │                             ▼                                             │
│         │                        GATE DE CALIDAD (Claude revisor + humano opcional) │
│         │                        rúbrica de marca/claims/calidad; en modo           │
│         │                        supervisado pide aprobación por Teams              │
│         │                             │                                             │
│         │                             ▼                                             │
│         └──────────────────────  ORQUESTADOR DE PUBLICACIÓN (determinista)          │
│                                  scheduler + ledger anti-duplicado + retries        │
└──────────────────────────────────────┬─────────────────────────────────────────────┘
                                       │ puerto Publisher / puerto Metrics
        ┌──────────────┬───────────────┼───────────────┬──────────────┐
        ▼              ▼               ▼               ▼              ▼
   TikTokAdapter  InstagramAdapter  YouTubeAdapter  FacebookAdapter  (futuro…)
   (Zernio/Buffer  (futuro)          (futuro)        (futuro)
    hoy; app
    propia después)
```

**Contratos clave (lo que hace la multi-red barata de agregar):**

- `ContentPackage` — unidad de contenido agnóstica: guion, assets fuente (audio, clips,
  imágenes), caption maestro, hashtags maestro, CTA, cover. Un package puede derivar en
  N `PlatformRendition`.
- `PlatformProfile` — reglas declarativas por red (YAML, patrón `tenants/` existente):
  aspect ratio, duración min/max, límites de caption, número de hashtags, tono de copy,
  frecuencia objetivo, ventanas horarias. El Planificador y el Productor consumen el
  profile; **adaptar a Instagram = escribir un profile + un adapter**, no tocar el core.
- `Publisher` (puerto) — `publish(rendition, when) -> post_id`, `get_status(post_id)`.
- `MetricsSource` (puerto) — `fetch_metrics(post_id) -> {views, likes, comments, shares}`.

**Reutilización directa del repo actual (probado en producción):**

| Pieza existente | Uso aquí |
|---|---|
| `safe_json` / locks | Todo el state local del pipeline |
| `send_ledger` (patrón) | `publish_ledger`: un post jamás se publica dos veces ni se pierde en silencio |
| `_reliable_job` + APScheduler | Jobs del orquestador |
| `news_brief.py` (patrón Claude+web search) | Brief de tendencias del nicho |
| Bot de Teams + Adaptive Cards | Gate de aprobación, tarjeta diaria de la historia, alertas de error |
| `graph_mail` | Reporte semanal del Media Manager a gerencia |
| `core_config.py` / `tenants/*.yaml` | Config por tenant: cuenta, volumen, pilares, modo de autonomía |

### 4.1 Base de datos

**SQLite** (archivo por tenant, WAL) en Fase 1 — cero infra nueva, transaccional, se
respalda como archivo. Migración a **Azure PostgreSQL** en Fase 5+ (multi-tenant real).

Tablas: `ideas` (backlog puntuado), `content_packages`, `renditions`, `schedule_slots`,
`publish_ledger`, `metrics_snapshots` (serie temporal por post), `experiments`
(dimensión, variante, resultado), `playbook` (aprendizajes vigentes del Analista,
versionados), `system_events` (errores/decisiones para el dashboard).

### 4.2 Dónde corre

- **Fase 1–2:** worker en la PC de Mateo (render local con GPU/CPU disponible) — igual
  que el Apollo notifier, asumiendo su misma limitación SPOF ya conocida.
- **Fase 3+:** contenedor en **Azure Container Apps Job** (el render Remotion necesita
  Node+Chrome; los App Service actuales son Python). El scheduler dispara el job; los
  assets van a Azure Blob Storage. Publicación y métricas (ligeras) pueden vivir en el
  App Service existente.

### 4.3 Dashboard

**FastAPI + HTML server-rendered** (mismo stack del bot, sin framework JS nuevo), rutas
`/media/*` protegidas con el patrón de admin token del bot:

- **Hoy:** cola del día, estados (idea → guion → render → QA → programado → publicado → medido).
- **Calendario:** semana/mes, slots, formatos, hover con preview.
- **Contenido:** galería de renders con su copy completo, preview de video/carrusel.
- **Métricas:** serie por post y agregados por pilar/gancho/formato/hora; curva de followers.
- **Playbook:** qué ha aprendido el sistema (legible: "los videos de tips de <35s a las
  19h rinden 2.3x"), historial de experimentos.
- **Salud:** errores, jobs, latencias, budget de API consumido.
- **Ideas:** backlog puntuado con el porqué de cada score.

---

## 5. Pipeline de producción (detalle técnico)

### 5.1 Video (por ítem, ~5–8 min de proceso)

1. **Guion** — Claude (Sonnet): estructura hook (≤2s) → desarrollo → CTA, con el
   playbook vigente y `company_context.md` como system prompt. Salida JSON validada.
2. **Voz** — **Azure Speech neural** (`es-EC-AndreaNeural`/`es-MX-DaliaNeural`): $0/mes
   (free tier F0 = 500k chars; consumo estimado 150k). Los **word boundaries del TTS**
   dan timestamps exactos → no hace falta Whisper para nuestros propios videos.
   A/B previsto contra ElevenLabs ($22/mes) si el rendimiento lo justifica.
3. **Visuales** — b-roll de **Pexels/Pixabay API** (gratis) curado por keywords del
   guion + overlays de marca. Fase futura opcional: video generativo (Kling/Veo) solo
   si un experimento lo justifica — no es requisito del MVP.
4. **Subtítulos karaoke** — `@remotion/captions` → `createTikTokStyleCaptions()` con
   los timestamps del TTS (sync perfecto, sin transcripción).
5. **Render** — **Remotion** (licencia gratis para equipos ≤3 — nuestro caso; template
   oficial TikTok como base). 1080×1920 H.264. Local en Fase 1, Container Apps después.
6. **Portada** — frame elegido + título overlay (mismo template Remotion);
   `video_cover_timestamp_ms` al publicar.
7. **QA técnico automático** — duración/resolución/loudness/legibilidad de subtítulos +
   revisor Claude con rúbrica de marca (claims prohibidos, tono, CTA presente).

*Plan B de render (si Remotion excede el esfuerzo en Fase 2):* JSON2Video ($50/mes,
subtítulos TikTok integrados) — mismo puerto `Renderer`, decisión reversible.

### 5.2 Carruseles

Plantillas HTML de marca → screenshot 1080×1920 con Playwright headless (uso local
legítimo, no automatiza TikTok) → 5–10 slides → photo post API (título 90 chars,
descripción 4000, `auto_add_music`).

### 5.3 Historia diaria (asistida)

Asset 9:16 + caption generados y encolados → tarjeta de Teams a las 18:00 con el
archivo listo → publicación manual en ~30 s. Métrica de cumplimiento en el dashboard.

---

## 6. Autonomía, seguridad y gobernanza

### 6.1 Niveles de autonomía (configurables por tenant)
- **L0 supervisado (semanas 1–4):** todo pasa por aprobación en Teams (aprobar /
  regenerar / descartar, un tap). Meta: calibrar el gate de calidad con feedback real.
- **L1 semi-autónomo:** publica solo lo que el revisor Claude puntúa ≥ umbral; el resto
  pide aprobación. Kill-switch por Teams (`/media pause`).
- **L2 autónomo (objetivo):** publica todo; humano solo recibe reporte semanal y alertas.
  Se activa cuando L1 lleve ≥2 semanas sin rechazos humanos de contenido auto-aprobado.

### 6.2 Reglas duras (nunca configurables por LLM)
Presupuesto diario de posts, ventanas horarias permitidas, lista de claims prohibidos
(regulatorio: "biodegradable certificado" solo con cita de certificación), nunca
responder/interactuar con terceros (fuera de alcance v1), credenciales solo en env
vars/Key Vault, `publish_ledger` inviolable.

### 6.3 Riesgos principales

| Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|
| Zernio (startup) cierra o cambia pricing | Media | Medio | Puerto `Publisher`: swap a Buffer/Ayrshare en horas; auditoría propia en curso desde Fase 4 |
| Rechazo de auditoría propia | Media | Bajo (seguimos en terceros) | Presentarla como SaaS VER-IA multi-tenant, no herramienta interna |
| Contenido repetitivo → demonización | Media | Alto | Dedup semántico entre guiones, rotación de pilares, regla anti-near-duplicate en QA |
| Error de marca/claim publicado | Baja | Alto | Gate L0/L1 + lista de claims + revisor Claude |
| PC de Mateo apagada (Fase 1–2) | Alta | Medio | Cola persistente: al volver, publica lo pendiente dentro de ventana válida o reprograma; migración a Azure en Fase 3 |
| Cambios de API de TikTok | Media | Medio | Todo TikTok-específico vive en 1 adapter; tests de contrato |

### 6.4 Por qué NO autonomía total el día 1
Un sistema que publica 60+ piezas/mes con la marca de la empresa sin que nadie las vea
es un riesgo reputacional injustificado cuando el costo de mitigarlo son ~5 min/día de
taps en Teams durante 4 semanas. La autonomía se gana con track record medible, igual
que un empleado nuevo. El diseño L0→L2 hace ese tránsito explícito y reversible.

---

## 7. Plan de implementación por fases

| Fase | Alcance | Criterio de salida | Duración est. |
|---|---|---|---|
| **F0 — Fundaciones** | Módulo `media_manager/`, modelos de datos, SQLite, config tenant, puertos (Publisher/Renderer/Metrics), tests | Esquema estable + CI verde | 1 semana |
| **F1 — Producción** | Pipeline guion→TTS→render Remotion→QA. Primer video demo aprobado por ustedes. Carruseles HTML→PNG | 10 videos de prueba con calidad aceptada | 2 semanas |
| **F2 — Publicación** | Adapter Zernio + ledger + scheduler + modo L0 con aprobación Teams. Cuenta conectada. Primeros posts públicos reales | 1 semana publicando 2/día sin incidentes | 1–2 semanas |
| **F3 — Inteligencia** | Ingesta de métricas, scoring, playbook, Analista, experimentos e2e. Tarjeta diaria de historia | Primer ciclo de aprendizaje cerrado (métrica → decisión → post) | 2 semanas |
| **F4 — Dashboard + autonomía L1** | Dashboard completo, kill-switch, reporte semanal a gerencia. Solicitud de auditoría TikTok propia (VER-IA) en paralelo | L1 activo; auditoría enviada | 2 semanas |
| **F5 — Escala** | L2, subir volumen según datos (→ ¿6/día?), migración del render a Azure Container Apps, hardening multi-tenant | Volumen objetivo sostenido 2 semanas | 2–3 semanas |
| **F6 — Segunda red** | `InstagramAdapter` + profile (Reels/carruseles/stories vía Graph API — esa SÍ tiene scheduling y stories para cuentas Business) | Mismo core publicando en 2 redes | 2 semanas |

Total a sistema autónomo en TikTok: **~8–10 semanas** de trabajo incremental, con
valor visible desde F1 (semana 3).

---

## 8. Costos estimados (mensuales, escenario 60–90 posts/mes)

| Concepto | Recomendado | Premium |
|---|---|---|
| Guiones + estrategia + análisis (Claude API, Sonnet) | $15–40 | $40–80 (más volumen) |
| TTS | Azure F0: **$0** | ElevenLabs Creator: $22 |
| B-roll / imágenes | Pexels/Pixabay: **$0** | + stock premium/gen-AI: $30–60 |
| Render | Remotion local: **$0** | JSON2Video: $50 |
| Publisher | Zernio (1–2 ctas): **$0** / Buffer Free | Ayrshare: $149 |
| Música licenciada | biblioteca CC/una compra única | Epidemic sound: ~$18 |
| Infra Azure extra (Fase 3+) | $5–15 (Blob + Container Job) | $20–40 |
| **Total** | **~$30–80/mes** | **~$230–380/mes** |

A 6 videos/día (180/mes) el costo Claude+render escala ~2.5×; el resto casi no cambia.

---

## 9. Mantenimiento y escalabilidad

- **Mantenimiento previsto:** rotación anual del OAuth de TikTok (lo exige la
  plataforma), revisión mensual del catálogo de música, actualización del profile
  cuando TikTok cambie límites (1 YAML), dependencias vía CI existente.
- **Escala a 50 tenants (visión VER-IA):** el diseño por tenant (config YAML + DB por
  tenant → Postgres compartido con `tenant_id`) es el mismo patrón ya congelado en
  `PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md`. El render es el único componente que
  necesita cola con workers (Container Apps escala horizontal). La app propia auditada
  (F4) elimina el costo por-tenant del publisher.
- **Multi-red:** cada red nueva = 1 adapter + 1 profile + su flujo OAuth. Instagram y
  Facebook (Graph API) son las más completas (tienen stories + scheduling nativo);
  YouTube Shorts (Data API v3) es directa; X/Pinterest/LinkedIn tienen APIs de
  publicación estables. TikTok resulta ser, irónicamente, la red MÁS restrictiva de
  todas las listadas — otra razón para que el core no se acople a ella.

---

## 10. Decisiones que necesito de ustedes antes de F0

1. **Volumen inicial:** ¿aceptan arrancar con 2 videos/día + 3 carruseles/semana y
   escalar por datos, o insisten en 6/día desde el día 1? (Todo lo demás es idéntico.)
2. **Historia diaria:** ¿aceptan el modo asistido (30 s/día de publicación manual) como
   única opción honesta?
3. **Publisher:** ¿OK con Zernio gratis (startup joven) o prefieren pagar Ayrshare
   ($149/mes) por madurez desde el día 1?
4. **Voz:** ¿arrancamos con Azure ($0, acento es-EC disponible) y hacemos A/B contra
   ElevenLabs, o directo ElevenLabs ($22)?
5. **Cuenta TikTok objetivo:** ¿@biodegradablesecuador o una cuenta nueva de prueba
   para F2? (Recomiendo cuenta de prueba 2 semanas, luego la real.)
6. **Nicho/pilares de contenido:** necesito 3–5 pilares aprobados por gerencia
   (propuesta inicial: educación sostenibilidad, producto en uso, detrás de cámaras,
   tips para negocios food-service, tendencias eco).

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
