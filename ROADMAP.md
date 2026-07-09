# ROADMAP — Marketing Brain sobre VER-OS

**Fuente única de verdad del progreso del proyecto.** Se actualiza en cada PR que
avance una tarea. Línea base aprobada (2026-07-06): `PROPUESTA_VER_OS.md` v0.1 +
`PROPUESTA_TIKTOK_MEDIA_MANAGER.md` v3.

**Estados:** ⬜ pendiente · 🔨 en curso · ✅ hecho · ⛔ bloqueado · 👤 acción humana

**Regla de cierre de fase (4 condiciones):** (1) software ejecutable y demostrable;
(2) pruebas automatizadas + documentación mínima; (3) alcance cerrado por completo;
(4) revisión técnica: ¿lo aprendido exige ajustar VER-OS v0.1 antes de seguir?
El registro de esas revisiones vive al final de este archivo.

**Reglas permanentes del board (2026-07-06), aplican a toda fase:**
1. **Deuda técnica explícita:** cada fase cierra con su tabla de deuda creada
   (impacto, prioridad, fase recomendada de resolución) en §Deuda técnica.
2. **Cuestionar arquitectura en caliente:** si una decisión escrita deja de ser la
   mejor, se cuestiona de inmediato — no se desarrolla por inercia documental.
3. **Dependencias nuevas solo si valen su mantenimiento:** sistema pequeño y
   entendible antes que lleno de librerías. Toda dependencia nueva se justifica
   por escrito en el PR.
4. **VER-OS es consecuencia de la implementación:** si Marketing descubre una
   mejor forma, primero se actualiza el estándar, después se sigue desarrollando.
5. **Demostración funcional obligatoria:** una fase sin forma objetiva de
   comprobar que funciona no está terminada.
6. (Del validador de contratos, condición del board:) si empieza a crecer en
   complejidad o a replicar funcionalidades maduras de `jsonschema`, señalarlo
   ANTES de seguir ampliándolo.

**Directrices del board (2026-07-07), F1 en adelante:**
7. **Resultados, no contenido:** cada pieza es un experimento controlado con
   hipótesis de negocio explícita (qué aprendemos, qué métrica decide, qué
   decisión sigue de cada resultado). Enforced en el modelo: `ContentPackage`
   sin `hypothesis` no valida.
8. **Independencia de plataforma vigilada:** si una decisión empieza a acoplar
   el core a una red específica, señalarlo de inmediato antes de continuar.
9. **Calidad sobre cantidad:** consistencia excelente antes que cuota fija; si
   los datos sugieren otra frecuencia, el sistema lo PROPONE al board con
   evidencia (no lo cambia solo).
10. **Todo agente nuevo nace medible:** métricas de desempeño claras
    (¿qué guionista/modelo/CTA/estilo rinde más?). Componente inmedible =
    componente inoptimizable. `generated_by` en cada artefacto + metering.
11. **Lente de producto:** funcionalidad que solo sirve a un cliente se señala
    y se propone su generalización (datos del cliente a `tenants/`, lógica a
    módulos genéricos).

**Directrices del board (2026-07-07, tras F1.5):**
12. **Economía sublineal:** el costo marginal por pieza tiende a cero. Orden de
    palancas: caching de prompts → contexto destilado → modelo más barato con
    A/B de calidad → Batch API → open source local SOLO con evidencia de
    calidad comparable (experimento gated, no default). Meta F1: <$0.01/video.
13. **Duración estándar 20–30 s.** Ningún componente produce piezas más largas
    salvo evidencia experimental de que un formato específico rinde mejor.
14. **Telemetría de eficiencia permanente:** cada etapa registra tiempo, costo,
    tokens y reuso de caché (unidad `stage_ms` del meter). Lo inmedible no se
    optimiza.

---

## Decisiones adoptadas (línea base aprobada) y pendientes humanas

| Decisión | Estado |
|---|---|
| Volumen inicial 2 videos/día + 3 carruseles/semana, escalado por datos | ✅ adoptada |
| Historia diaria en modo asistido (tarjeta Teams + 30 s manuales) | ✅ adoptada |
| Publisher: Zernio (fallback Buffer Free) tras puerto `Publisher` | ✅ adoptada |
| Voz: Azure TTS es-EC ($0) con A/B vs ElevenLabs | ✅ adoptada |
| Cuenta TikTok de prueba 2 semanas → cuenta real | ✅ adoptada |
| Secuencia org: kernel en F0, CEO etapa 1 en F7, CEO real con ≥2 deptos | ✅ adoptada |
| Segundo departamento: Comercial (contrato `LeadOutcome`) | ✅ adoptada |
| **OKRs numéricos del trimestre para el charter** (propuesta: 0→3k seguidores, ≥2 leads/sem, ≥12 posts/sem) | 👤 confirmar cifras — bloquea F2 (no F0/F1) |
| **Pilares de contenido**: educación sostenibilidad, producto en uso real, tips food-service, detrás de cámaras, tendencias eco EC — **como HIPÓTESIS iniciales**, el sistema los modifica cuando los datos lo demuestren (no son reglas fijas) | ✅ confirmados 2026-07-06 |
| **Separación corporativa VER-IA** (M365 + Azure + GitHub org + acuerdo IP) | 👤 ya trackeada como F1 de plataforma (CLAUDE.md pendiente #5) — no bloquea el código, bloquea venta |
| Alta de cuenta Zernio/Buffer + cuenta TikTok de prueba | 👤 antes de F2 |

---

## Fase 0 — Kernel VER-OS (`org/`)

**Objetivo:** los 12 componentes del estándar en versión mínima honesta, ejecutables
y testeados. Sin dominio de marketing todavía.
**Demostrable con:** `python -m pytest tests/test_veros_kernel.py -q` + demo de un
departamento de juguete emitiendo eventos/journal/metering.
**Nota de integración:** la plataforma ya tiene metering LLM (`llm_usage.py`),
secrets por Key Vault (`integrations.yaml`) y config de tenant (`tenants/*/config.yaml`).
El kernel se integra con eso — no lo duplica.

| ID | Tarea | Depende de | Criterio de aceptación | Estado |
|---|---|---|---|---|
| F0.1 | Paquete `org/kernel`: storage port por tenant (SQLite WAL, un archivo por tenant) | — | Aislamiento entre tenants probado por test | ✅ |
| F0.2 | Bus de eventos: envelope estándar, append-only (triggers), consumo idempotente por `event_id` | F0.1 | UPDATE/DELETE sobre `org_events` falla a nivel SQL; `process()` 2ª vez → no-op | ✅ |
| F0.3 | Decision journal append-only con `correlation_id` | F0.1 | Inmutabilidad probada a nivel SQL; entradas ordenadas | ✅ |
| F0.4 | Metering por (tenant, dept, unidad) + corte duro de presupuesto | F0.1 | `BudgetExceeded` al superar `budget_usd_month`; suma mensual correcta | ✅ |
| F0.5 | Manifest `verops.yaml`: parser + validación (12 secciones, trust_tier, autonomía) | — | Manifests inválidos rechazados con lista de errores | ✅ |
| F0.6 | Máquinas de estado: autonomía L0→L3 (promoción con evidencia, demote libre) y ciclo de vida (proposed→retired) | F0.1 | Transiciones inválidas lanzan; promociones exigen evidencia y respetan `max_level` del manifest | ✅ |
| F0.7 | `Department`: composición de todo lo anterior + capacidades enforced + `health()` | F0.1–F0.6 | Capability no otorgada → `CapabilityError`; health reporta estado/gasto/último evento | ✅ |
| F0.8 | Contratos fundacionales en `org/contracts/` (envelope + `LeadHandoff@1`, `LeadOutcome@1`, `WeeklyDeptReport@1`, `EscalationRequest@1`) + registro/validación | F0.5 | Payload inválido contra su esquema → rechazo con detalle | ✅ |
| F0.9 | Demo ejecutable (`python -m org.demo`): departamento de juguete completo (install→onboard→L0, decide, emite, mide, health) | F0.7 | Corre sin red ni secrets; salida legible | ✅ |
| F0.10 | Integración de metering con `llm_usage.py` (las llamadas LLM de un dept se reflejan en su meter) | F0.4 | Test de doble registro (llm_usage + meter del dept) | ✅ |
| F0.11 | Doc mínima `docs/ver-os-kernel.md` (qué hay, cómo usarlo, qué es convención vs invariante) | F0.1–F0.9 | Existe y refleja el código real | ✅ |
| F0.12 | **Revisión técnica de fase** con el board: ¿ajustar VER-OS v0.1? | todo F0 | Acta en §Revisiones de este archivo | ✅ |

**FASE 0 CERRADA — 2026-07-06** (aprobación del board en la revisión técnica).

## Fase 1 — Producción de contenido 🔨 EN CURSO

**Objetivo:** pipeline guion→TTS→render→QA + carruseles, sin publicar.
Pilares confirmados por el board 2026-07-06 (como hipótesis) — desbloqueada.

| ID | Tarea | Depende de | Criterio de aceptación | Estado |
|---|---|---|---|---|
| F1.1 | Modelos `ContentPackage`/`PlatformRendition` + `PlatformProfile` YAML (TikTok) + pilares como hipótesis en `tenants/<slug>/marketing.yaml` | F0 | Validación pydantic estricta (patrón `core/config/schema.py`) | ✅ |
| F1.2 | Guionista (Claude Sonnet, JSON validado, registra en `llm_usage`+meter) + `Hypothesis` obligatoria en el modelo (directriz #7) + `marketing/brand.py` | F1.1 | 10 guiones válidos consecutivos sin intervención | ✅ (10/10 el 2026-07-07, $0.046/guion, 0 intervenciones) |
| F1.3 | TTS Azure neural es-EC con word boundaries persistidos (`marketing/tts.py`, voz como dato del tenant, SDK justificado en `requirements-marketing.txt`) | F1.1 | Audio + timestamps por palabra en el package | ✅ (2026-07-07: guion real → 6 MP3 es-EC + 127 WordTimings, $0 tier F0) |
| F1.4 | B-roll Pexels por keywords del guion (`marketing/broll.py`: backend inyectable, dedup por package, fallback al pilar, cache por archivo) | F1.2 | Assets descargados y atribuidos en el package | ✅ (2026-07-07: 4 clips verticales reales, únicos, atribuidos, scene_index para el render) |
| F1.5 | Render Remotion 1080×1920 + subtítulos karaoke desde timestamps TTS + portada (`marketing/render_video.py` + template React en `marketing/render/`; Node 22 portable en `C:\Users\Mateo\tools`) | F1.3, F1.4 | Video H.264 válido; QA técnico automático pasa (loudness → deuda F1) | ✅ (2026-07-07: pipeline completo real guion→voz→b-roll→MP4 31MB/31s `produced`, $0.049) |
| F1.6 | Carruseles → PNG 1080×1920 (**cambio justificado:** stills de Remotion en vez de HTML+Playwright — misma estética que el video, CERO dependencias nuevas, un solo stack de plantillas; regla #3) | F1.1 | 5–10 slides de marca desde un package | ✅ (2026-07-07: 7 PNG reales de marca desde guion Claude) |
| F1.6b | **Eficiencia (directrices #12-14):** prompt caching (system estable por tenant+formato), duración estándar 20-30 s en brief+prompt+telemetría, telemetría `stage_ms` (tiempo/tokens/cache/reuso por etapa) en guion/tts/broll/render/carousel + `stage_stats()` | F1.2–F1.6 | cache_read > 0 verificado; guiones en 55-80 palabras; stats por etapa consultables | ✅ (2026-07-07: 5.636 tokens de cache leídos → **$0.0129/guion, -72%**; guiones reales de 57 y 68 palabras) |
| F1.7 | Gate de calidad en 2 capas (`marketing/gate.py`): checks deterministas $0 (estado, assets, duración 20-30s, límites de red, claims del charter — rechazo sin gastar LLM) → revisor Claude con rúbrica de marca (score ≥75) | F1.2 | Pieza con claim vetado → rechazada con razón | ✅ (2026-07-07; verificación con pieza real saboteada en el lote F1.8) |
| F1.8 | Demo: 10 piezas (8 videos + 2 carruseles) + 1 saboteada, pipeline completo con gate real | F1.5–F1.7 | 👤 gerencia aprueba calidad | ✅ lote producido 2026-07-09; 0/10 aprobadas por el propio gate — el board APROBÓ el diagnóstico ("problema de iteración, no de pipeline") |
| F1.9 | Revisión técnica de fase + retrospectiva formal | todo F1 | Acta | ✅ acta abajo; `docs/retro-fase1.md` |

**FASE 1 CERRADA — 2026-07-09** (veredicto del board: estándar de calidad
funcionando; el QA no es trámite; F2 empieza por el ciclo de reparación, NO por
el publisher).

## Fase 2 — Iteración + robustez + publicación L0 (TikTok)

**KPI principal (board 2026-07-09): First Pass Yield (FPY)** — % de piezas que
pasan el gate al primer intento. **Objetivo: >80%.** Cuando lo alcancemos, el
sistema genera contenido publicable de forma autónoma. Métricas por intento:
motivo de rechazo, cambios realizados, resuelto sí/no, tiempo y costo extra.

### F2.0 — Ciclo de reparación y robustez (ANTES del publisher — orden del board)

| ID | Tarea | Criterio | Estado |
|---|---|---|---|
| F2.0a | Reglas duras de estilo en el guionista (sin emojis, UN solo CTA, no inventar datos/cifras fuera del contexto, 60-78 palabras) + checks deterministas nuevos en el gate (regex emojis, duración estimada en borrador) | Violación de estilo → rechazo $0 sin LLM | ✅ 2026-07-09 |
| F2.0b | `review_copy` (gate sobre BORRADOR, pre-producción) + ciclo Generador→Gate→Feedback→Reparación→Gate (máx 2 reparaciones); cada intento registra motivo/cambios/resuelto/tiempo/costo en journal+meter | Pieza con defecto reparable → aprobada en ≤3 intentos; todo auditado | ✅ 2026-07-09 (el ciclo corre sobre el borrador: reparar copy = centavos; el gate final post-producción se mantiene) |
| F2.0c | KPI FPY: evento `content.copy_review` por intento + `fpy_stats()` (FPY, % reparadas, categorías de error frecuentes) | FPY consultable por mes; base del dashboard F4 | ✅ 2026-07-09 |
| F2.0d | Cola persistente de packages (tabla en el TenantStore: estados draft→copy_approved→produced→qa_approved→scheduled→published/rechazado, resumible tras crash) | Kill del proceso a mitad de lote → reanuda sin duplicar ni perder | ⬜ |
| F2.0e | Render robusto: `<Video>`→`OffthreadVideo` + duración del clip desde la API de Pexels (fix del fallo 3× reproducible) | La pieza 4 del lote F1.8 (mesa de evento) se produce | ⬜ |
| F2.0f | Validación: lote copy-level real (≥10 briefs) midiendo FPY inicial y efectividad de reparación | Primer datapoint de FPY publicado en ROADMAP | ⬜ |

### Integración TikTok — DIFERIDA por decisión del board (2026-07-09)

**No se conecta ninguna cuenta (ni de prueba) hasta que TODAS las fases del
sistema estén completas y estables.** Cuando llegue ese momento, será una fase
propia con: integración del método de publicación seleccionado, manejo seguro
de credenciales, cuenta de pruebas, monitoreo de errores y validación del flujo
completo — y solo después, cuentas reales. Las tareas F2.1–F2.6 originales
(publisher/ledger/scheduler/L0) se reprograman a esa fase. Las acciones humanas
"cuenta Zernio/Buffer + cuenta TikTok de prueba" dejan de ser pendientes activos.

### F2.1+ — continúa el desarrollo interno según roadmap (sin publicar)

| ID | Tarea | Depende de | Criterio | Estado |
|---|---|---|---|---|
| F2.1 | Puerto `Publisher` + adapter Zernio (y fallback Buffer) | F0 | Publica video y carrusel a cuenta de prueba | ⬜ |
| F2.2 | `publish_ledger` (patrón `send_ledger`): jamás dos veces, jamás perdido en silencio | F2.1 | Test de doble disparo y de crash a mitad | ⬜ |
| F2.3 | Scheduler de slots (ventanas horarias del charter, anti-repetición) | F2.1 | Slots respetan ventanas y espaciado mínimo | ⬜ |
| F2.4 | Gate L0: tarjeta Teams aprobar/regenerar/descartar (infra bot existente) | F1.7 | Nada se publica sin aprobación en L0 | ⬜ |
| F2.5 | Etiquetado experimental obligatorio de cada post (pilar/gancho/formato/franja/CTA) | F0.8 | Post sin etiquetas completas → no publicable | ⬜ |
| F2.6 | 1 semana a 2 videos/día en cuenta de prueba sin incidentes | F2.1–F2.5 | Ledger limpio, 0 duplicados, 0 huérfanos | ⬜ |
| F2.7 | Revisión técnica de fase | todo F2 | Acta | ⬜ 👤 |

## Fase 3 — Ciclo de aprendizaje

| ID | Tarea | Criterio | Estado |
|---|---|---|---|
| F3.1 | `MetricsSource` TikTok (views/likes/comments/shares + followers) con snapshots | Serie temporal por post en DB | ⬜ |
| F3.2 | Scoring normalizado por hora/edad + experiment registry | Score reproducible en test | ⬜ |
| F3.3 | Analista: actualización de playbook CON procedencia (regla→exp_ids→confianza) | Ninguna regla sin evidencia n≥5; decay temporal | ⬜ |
| F3.4 | Asignación 80/20 explota/explora en el Planificador | Distribución verificada en test | ⬜ |
| F3.5 | Tarjeta diaria de historia asistida (asset+caption listos) | Entrega 18:00 + métrica de cumplimiento | ⬜ |
| F3.6 | Primer ciclo cerrado métrica→regla→post mejorado | Journal lo evidencia end-to-end | ⬜ |
| F3.7 | Revisión técnica de fase | Acta | ⬜ 👤 |

## Fase 4 — Autonomía L1 + dashboard + self-report

| ID | Tarea | Criterio | Estado |
|---|---|---|---|
| F4.1 | Gate de auto-aprobación por umbral (L1) + kill-switch `/marketing pause` | Umbral configurable en charter; pause inmediato | ⬜ |
| F4.2 | Dashboard `/media/*` (FastAPI server-rendered, auth patrón admin_api) | Hoy/calendario/galería/playbook/salud/costos | ⬜ |
| F4.3 | Self-report semanal a gerencia (contrato `WeeklyDeptReport@1` → graph_mail) | Primer reporte real enviado | ⬜ |
| F4.4 | Solicitud de auditoría TikTok propia (app VER-IA) — en paralelo | 👤 materiales enviados | ⬜ |
| F4.5 | Revisión técnica de fase (incluye criterio L0→L1: <10% rechazo humano 2 semanas) | Acta | ⬜ 👤 |

## Fase 5 — Comunidad · Fase 6 — Canal SEO/web · Fase 7 — L2/flota/CEO etapa 1

Detalle en `PROPUESTA_TIKTOK_MEDIA_MANAGER.md` §7; se expandirán a tareas aquí al
cerrar F4 (regla: no se detalla backlog a más de 2 fases vista — se detalla con lo
aprendido, no con lo imaginado).

Hitos gruesos: F5 triage comentarios→borradores→auto dentro de política ·
F6 reuso de contenido ganador como posts SEO vía `wp_client`/`wp_apply` ·
F7 L2, volumen por datos, render en Azure Container Apps, Profession Brain,
onboarding marca #2 (Andex), CEO Agent etapa 1.

---

## Registro de revisiones técnicas de fase

| Fase | Fecha | Decisión sobre VER-OS | Acta |
|---|---|---|---|
| F1 | 2026-07-09 | **Sin cambios a v0.1.** Veredicto del board: el rechazo 10/10 del lote demuestra que el estándar funciona ("me da más confianza que aprobar contenido mediocre"); el problema es de ITERACIÓN, no de pipeline ni arquitectura. Decisiones: F2 arranca con el ciclo de reparación (flujo Generador→Gate→Feedback→Reparación→Gate, máx 2 reparaciones, todo registrado); **FPY = KPI principal, objetivo >80%**; OffthreadVideo y cola persistente ANTES del scheduler; honestidad como política permanente. Aprendizajes → backlog v1.0: persistencia de artefactos de dominio desde F0 del departamento; colas resumibles como norma para pipelines largos. | `docs/retro-fase1.md` |
| F0 | 2026-07-06 | **Sin cambios a v0.1.** Ratificadas las 3 decisiones de implementación: (1) SQLite por tenant con enforcement del motor (camino limpio a Postgres+RLS en H2); (2) validador de contratos propio, con la condición de señalar ANTES de ampliarlo si empieza a replicar jsonschema (regla permanente #6); (3) idempotencia por claims. **Aprendizaje promovido al backlog de v1.0:** la separación registro-de-metering (best-effort, jamás lanza) vs enforcement-de-presupuesto (duro, antes de gastar) entra al estándar como aprendizaje extraído, no como supuesto. | Board aprobó cierre; 5 reglas permanentes nuevas (arriba) |

## Deuda técnica (regla permanente #1)

### Creada en F0

| Deuda | Impacto | Prioridad | Resolver en |
|---|---|---|---|
| SQLite por tenant → PostgreSQL+RLS (backups/migraciones/operación a ≥5 tenants) | Escala operativa | Media | H2 (deuda deliberada, detrás del puerto de storage) |
| Validador de contratos propio (tipos+required+enum) | Mantenimiento si crece | Baja | Vigilancia continua (regla #6); decidir en v1.0 del estándar |
| `TenantStore` es single-process (lock de hilos, no cross-proceso) — coherente con el "1 worker deliberado" de la plataforma | Concurrencia futura | Baja | H2, junto con Postgres |
| `health()` no emite heartbeat a ningún control plane (no existe aún) | Observabilidad de flota | Baja | F7 (control plane) |
| Sin CLI de inspección de journal/eventos (solo API Python y demo) | DX/auditoría manual | Baja | F4 (dashboard los expone) |

### Creada en F1 (fase abierta — se consolida al cierre)

| Deuda | Impacto | Prioridad | Resolver en |
|---|---|---|---|
| QA de loudness no implementado (exigiría ffmpeg/pyloudnorm — dependencia nueva) | HOY bajo: el audio es 100% TTS Azure con nivel consistente entre piezas. Se vuelve real cuando se mezcle MÚSICA (aún no construido) | Baja→Media | Junto con la mezcla de música (F2+), no antes — regla #3 |
| `render/public/<pkg>/` no se limpia tras el render | Disco crece con cada video | Baja | F1.8 (limpieza post-QA) |
| Tiempo de render en estado estable sin medir (la 1ª corrida incluyó descargas: 827s totales) | Estimación de throughput | Baja | F1.8 (medir en la demo de 10 piezas) |
| Node portable + Chrome de Remotion viven solo en la PC de Mateo | SPOF conocido del plan | Media | F7 (Container Apps Job) |
| `<Video>` del browser en el template → **3 fallos reproducibles** en el lote (el cap ≤2048 NO alcanzó: es el decode del browser, no la resolución) | Confiabilidad del render — pieza 4 imposible de producir | **Alta** | **F2.0** (refactor a OffthreadVideo + duración del clip desde la API de Pexels) |
| Sin ciclo de reparación gate→guionista (las piezas rechazadas mueren) | 0/10 aprobadas en el lote | **Alta** | **F2.0 — primera tarea de F2** |
| Sin persistencia de ContentPackages (viven en memoria; auditoría los reconstruyó desde props) | Operabilidad/auditoría | Alta | F2.0 (cola persistente) |
| Guionista sin reglas duras de estilo (emojis 7/10, CTA 3×, duración mínima floja) | Tasa de rechazo del gate | Alta | F2.0 (prompt + checks deterministas) |
