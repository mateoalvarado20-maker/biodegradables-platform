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
15. **(2026-07-10) Escepticismo ante resultados extraordinarios:** ningún
    problema se declara resuelto solo porque un experimento dio 100%. Ante un
    resultado extraordinario se asume primero sesgo/error de medición/muestra
    insuficiente y se intenta REFUTAR (prueba adversarial, más volumen, datos
    nuevos) antes de aceptar la conclusión. Referencia: sonda adversarial del
    revisor (run 3 FPY).
16. **(2026-07-10, F3) Métricas con propósito:** toda métrica almacenada debe
    responder, tarde o temprano, una pregunta de negocio (¿qué hook retiene?,
    ¿qué CTA convierte?, ¿qué pilar/horario/duración rinde?). Dato sin decisión
    asociada = dato que se cuestiona antes de almacenarse.
17. **(2026-07-10, F3) Analista conservador:** ningún cambio automático del
    playbook por un experimento exitoso único; todo cambio importante exige
    evidencia suficiente y nivel de confianza definido (y los estratégicos se
    PROPONEN al board — regla #9).
18. **(2026-07-10) KPIs de aprendizaje — Learning Velocity + Learning Accuracy:**
    LV mide qué tan rápido aprende el sistema (hipótesis evaluadas / confirmadas
    / descartadas, modificaciones reales del playbook, impacto posterior de cada
    aprendizaje). **LA mide qué tan CORRECTAMENTE aprende**: % de hipótesis
    confirmadas que siguen siendo correctas cuando llegan más datos. LV nunca se
    reporta sin LA — no se premia aprender rápido lo que luego hay que corregir.
19. **(2026-07-10) El sistema debe saber cuándo no sabe:** el Analista tiene 4
    veredictos (confirmada / rechazada / inconclusa / requiere más datos) y
    prefiere "no hay evidencia suficiente" antes que un aprendizaje falso. Toda
    conclusión incluye explícitamente: nivel de confianza, tamaño de muestra,
    evidencia utilizada, posibles factores de confusión, y qué datos
    adicionales subirían la confianza.
20. **(2026-07-10) El Analista nunca modifica el conocimiento — solo PROPONE:**
    entre Analista y Playbook existe el **Knowledge Manager**, único que decide
    si una propuesta se vuelve conocimiento. Toda propuesta incluye 8 campos:
    conocimiento a modificar, evidencia a favor, evidencia en contra, riesgos de
    aceptar, riesgos de no aceptar, impacto esperado, confianza, reversibilidad.
    Cada regla del playbook tiene MADUREZ (experimental → validada → consolidada
    → obsoleta) — una hipótesis nueva jamás pesa igual que una regla probada
    durante meses. Todo cambio es reversible con historial completo (quién
    propuso, con qué evidencia, qué la validó, cuándo/por qué cambió, qué
    impacto produjo).
21. **(2026-07-10) Toda capa nueva de arquitectura justifica su valor:** si una
    funcionalidad no mejora calidad del contenido, velocidad de producción,
    costos, capacidad de aprendizaje, facilidad de venta de VER-IA o
    mantenibilidad — se deja para una versión futura. La arquitectura base de
    aprendizaje se considera suficiente; el foco pasa al PRODUCTO.
22. **(2026-07-10) Contenido con propósito:** cada pieza existe porque ayuda a
    vender más o porque ayuda al sistema a aprender algo nuevo — nunca por
    generar volumen. El Planificador debe poder responder: qué publicar mañana,
    por qué, qué hipótesis valida, qué conocimiento explota, qué % explora, y
    qué se aprende aunque la pieza tenga pocas views.
23. **(2026-07-10) Optimizar por impacto de negocio, no por métricas aisladas:**
    todo experimento lleva PRIORIDAD DE NEGOCIO (awareness / engagement / leads
    / conversaciones / ventas / fidelización / educación del mercado); el
    scoring pondera distinto por objetivo y **jamás se comparan piezas con
    objetivos diferentes como equivalentes** (el Analista segmenta). El
    aprendizaje es acumulativo: el reporte semanal responde qué aprendimos, qué
    dejamos de creer, qué reglas nacieron/degradaron, qué experimentos tuvieron
    mayor retorno de aprendizaje y cuáles generaron valor comercial. El
    objetivo no es producir mejores videos: es tomar mejores decisiones cada
    semana que la anterior.
24. **(2026-07-10) PIVOT A VALOR — decisión de board:** el objetivo deja de ser
    construir capacidades y pasa a ser DEMOSTRAR VALOR. El motor de aprendizaje
    queda CONGELADO salvo correcciones críticas. Prioridad: operación diaria,
    estabilidad y validación con clientes reales. Toda funcionalidad nueva
    responde primero: **"¿esto nos acerca a un cliente pagando por VER-IA?"** —
    si no, versión posterior. Pensar como CTO de SaaS, no como investigador:
    producto estable, onboarding sencillo, operación confiable, cliente
    satisfecho, negocio escalable.
25. **(2026-07-10) KPI ejecutivo Time to Value (TTV):** días desde que un
    cliente instala VER-IA hasta su primer resultado tangible. KPI principal
    del producto junto a FPY y LV/LA. Propuesta de objetivo (a ratificar):
    TTV ≤ 7 días hasta el primer contenido aprobado listo, ≤ 14 hasta la
    primera publicación real.
26. **(2026-07-10) Nada está terminado si requiere intervención manual:**
    todo componente de la etapa MVP debe poder (a) reiniciarse sin perder
    información, (b) loguear claro, (c) medirse, (d) recuperarse de fallos,
    (e) ejecutarse programado, (f) supervisarse sin abrir el código. Los
    procesos que dependen de que un desarrollador los recuerde no existen.
    **KPI operativo: Hands-Off Rate (HOR)** — % del pipeline que corre sin
    intervención humana (las aprobaciones L0 son gobernanza por diseño, no
    cuentan como intervención; los rescates manuales sí). Objetivo del MVP:
    subir HOR progresivamente sin sacrificar calidad.
    **Formato de reporte CTO→CEO:** toda tarea importante se presenta
    respondiendo: qué problema de negocio resuelve, cómo acerca a un cliente
    pagando, cuál es el criterio objetivo de terminado, y qué riesgo
    introduce o reduce.

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
| F2.0d | Cola persistente de packages (`marketing/queue.py` + runner `pipeline.py`: estados persistidos en tabla `mkt_content_queue` del TenantStore, `submit/advance/run_pending`, errores de etapa con reintento acotado a 3 y revisión manual después) | Kill del proceso a mitad de lote → reanuda sin duplicar ni perder | ✅ 2026-07-10 — test de crash-resume verificado por metering (el render no se re-ejecuta tras reanudar); la sonda del juez ahora vive en `marketing/calibration_probe.py` como test de regresión de rúbrica |
| F2.0e | Render robusto: `<Video>`→`OffthreadVideo` + duración del clip desde la API + reintento ante fallo transitorio del compositor (esporádico en Windows tras descargar clips; sospecha Defender) | La pieza 4 del lote F1.8 (mesa de evento) se produce | ✅ 2026-07-10 — pieza "mesa de evento" producida E2E (24.3 MB); el reintento atrapó un fallo transitorio real en su primer uso (medido en telemetría `render_retried`) |
| F2.0f | Validación: lote copy-level real (≥10 briefs) midiendo FPY inicial y efectividad de reparación | Primer datapoint de FPY publicado en ROADMAP | ✅ 2026-07-09 — 3 runs de calibración (tabla abajo) + sonda adversarial 5/5 |

**Calibración FPY (mismos 10 briefs, cambios acumulativos):**

| Run | FPY | Aprobadas | Cambio introducido | Aprendizaje |
|---|---|---|---|---|
| 1 | 0% | 0/10 (0/30 intentos) | Reglas duras del generador + ciclo de reparación (primera medición) | Reglas duras funcionaron (emojis 7/10→~0, duración→3 casos); el juez "aprueba solo si perfecto" NUNCA aprueba — inútil |
| 2 | 10% | 6/10 (1 directa + 5 reparadas, éxito 56%) | Contrato del juez: BLOCKERS accionables vs MEJORAS; score = telemetría | Rechazos ya accionables; nueva clase dominante "contenido/CTA duplicado" = 50% artefacto (el juez leía el caption como duplicado del guion al recibir texto sin estructura) |
| 3 | **100%** | 10/10 al 1er intento (scores 81-88) | Superficies etiquetadas para el juez + política editorial CTA/caption en AMBOS prompts | $0.023/pieza y 25s/pieza (sin reparaciones); **sonda adversarial 5/5**: el juez sigue rechazando claim inventado, CTA intermedio, duplicación real y comparación con competidor, y aprueba la limpia |

Caveats honestos: n=10 con los mismos briefs — el FPY estable se medirá de forma
continua con briefs variados (F3); la sonda adversarial queda como test de
regresión de calibración (correr tras cada cambio de rúbrica).

### Integración TikTok — DIFERIDA por decisión del board (2026-07-09)

**No se conecta ninguna cuenta (ni de prueba) hasta que TODAS las fases del
sistema estén completas y estables.** Cuando llegue ese momento, será una fase
propia con: integración del método de publicación seleccionado, manejo seguro
de credenciales, cuenta de pruebas, monitoreo de errores y validación del flujo
completo — y solo después, cuentas reales. Las tareas F2.1–F2.6 originales
(publisher/ledger/scheduler/L0) se reprograman a esa fase. Las acciones humanas
"cuenta Zernio/Buffer + cuenta TikTok de prueba" dejan de ser pendientes activos.

**Demostración funcional de cierre de F2 (2026-07-10):** 2 briefs reales (video +
carrusel) por el flujo completo SOBRE LA COLA: submit (reparación) →
`run_pending` → gate final. Ambos `copy_approved` al 1er intento (FPY 1.0) y
ambos `qa_approved`: video 27.9 MB + portada, carrusel 7 slides. Costo total
$0.115, 10.9 min. Archivos: `~/.ver-os/demo-f2/`.

**FASE 2 CERRADA — 2026-07-10** (aprobación formal del board: 4 condiciones de
cierre cumplidas; 2 aprendizajes promovidos a VER-OS v1.0).

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

## Fase 3 — Ciclo de aprendizaje 🔨 EN CURSO

**Directrices del board para toda la fase:** métricas con propósito (#16),
Analista conservador (#17), KPI Learning Velocity (#18). Como la publicación
está diferida, el motor se valida contra un **simulador de métricas con sesgos
sembrados conocidos**: el criterio de aprendizaje es objetivo — el Analista
debe DESCUBRIR el sesgo que nosotros plantamos, con la evidencia y confianza
exigidas. El `MetricsSource` real de TikTok se enchufa en la fase de
integración sin tocar el motor (mismo puerto).

| ID | Tarea | Criterio | Estado |
|---|---|---|---|
| F3.1 | Métricas con propósito: puerto `MetricsSource` + snapshots persistidos donde CADA campo está mapeado a su pregunta de negocio (regla #16, mapa `PURPOSES` en `marketing/metrics.py`) + simulador `BiasedSimulator` con sesgos configurables, determinista, con curva de maduración | Campo sin pregunta → no se puede almacenar (validado); snapshots en serie temporal | ✅ 2026-07-10 (watch-time documentado como NO disponible, no olvidado) |
| F3.2 | Scoring normalizado (`marketing/scoring.py`): views proyectadas por curva de maduración + engagement ponderado por poder predictivo (shares 3.0 > comments 2.0 > saves 1.5 > likes 1.0; followers 4.0); mínimo 12 h de señal | Score reproducible y testeado; sin watch-time (límite documentado) | ✅ 2026-07-10 |
| F3.3 | Registro de experimentos (`marketing/experiments.py`) con los 4 veredictos de la regla #19 computados por t de Welch conservadora (sin LLM, sin deps nuevas): n≥5 por grupo, \|t\|≥2 media / ≥3 alta, efecto ≤10% con muestra = rechazada, detección de confusores (baja la confianza), historial append-only por hipótesis (base del KPI LA) | Sesgo sembrado → confirmada; sin sesgo → NO confirmada (control negativo); n chico → requiere_más_datos aunque el sesgo sea enorme | ✅ 2026-07-10 (6 tests de veredictos + confusores + historial) |
| F3.4 | Trío de conocimiento (regla #20): **Analista** (`analista.py` — observa/evalúa/PROPONE con los 8 campos; test de capas: no puede ni importar el playbook) → **Knowledge Manager** (`knowledge.py` — política determinista: crear=experimental; promoción solo con 2/4 confirmaciones consecutivas; degradación asimétrica: experimental muere directo, consolidada baja de a un nivel) → **Playbook** (`playbook.py` — revisiones append-only, madurez experimental→validada→consolidada→obsoleta, revert sin perder historial, peso por madurez para el Planificador) | **Descubre el sesgo sembrado y NO "descubre" sesgos inexistentes** (control negativo) | ✅ 2026-07-10 (8 tests: ciclo completo, escalera de madurez, contradicción, revert, capas) |
| F3.5 | Planificador como Media Manager (regla #22, `marketing/planner.py`): cada brief con propósito explícito — **explotar** reglas del playbook ponderadas por madurez (su hipótesis re-testea la regla → alimenta LA) o **explorar** produciendo exactamente los datos que el registro declaró faltantes (agenda = veredictos requiere_más_datos/inconclusa + catálogo sin medir); sin playbook → 100% exploración honesta; `explain()` responde las 6 preguntas del board; determinista, sin LLM | Distribución 80/20 verificada; todo brief con propósito completo | ✅ 2026-07-10 (+fix regla #19: los valores sub-muestreados ahora SÍ se registran como requiere_más_datos — antes el Analista los saltaba sin dejar constancia) — **FPY con briefs nuevos pendiente en F3.7 (demo)** |
| F3.6 | **Objetivos de negocio en todo el motor (regla #23)** + KPIs LV+LA (`learning_report.py`): `objective` obligatorio en piezas/briefs, scoring con pesos por objetivo (leads/sales con proxies honestos hasta `LeadOutcome`), Analista segmenta por objetivo (mezclar = error), conocimiento por objetivo (`regla:objetivo/dim=valor`); LV (evaluaciones + cambios reales del playbook) SIEMPRE con LA (% de confirmadas que sobreviven — sin re-evaluaciones devuelve None, no 100%); reporte semanal responde las 6 preguntas del board (aprendimos/dejamos de creer/nacidas/degradadas/retorno de aprendizaje/valor comercial) | LV y LA consultables, SIEMPRE juntos; reporte con las 6 preguntas; segmentación E2E testeada | ✅ 2026-07-10 (500 tests) |
| F3.7 | Primer ciclo cerrado: métrica (simulada) → veredicto → regla de playbook → brief del Planificador influido por la regla | Journal lo evidencia end-to-end | ⬜ |
| F3.8 | Tarjeta diaria de historia asistida | — | ➡️ movida a M3 (es parte del flujo de publicación TikTok) |
| F3.7 | Ciclo cerrado E2E: métrica → veredicto → regla → plan influido | Journal lo evidencia | ✅ 2026-07-10 — demo: regla `leads/hook=pregunta` creada y promovida; el plan 2 la EXPLOTA en 4/5 briefs; reporte LV+LA |
| F3.9 | Limpieza de `render/public/` post-gate (deuda F1/F2) | Staging no crece sin límite | ✅ 2026-07-10 (`cleanup_staging` tras el gate final, testeado) |
| F3.10 | Demo funcional + revisión técnica de fase | Acta | ✅ demo ejecutada — **FPY REAL con briefs nuevos del Planificador: 67% (4/6 al 1er intento, 2 reparadas con éxito, 0 rechazos definitivos)** — baseline honesto de producción vs el 100% de briefs de calibración; pendiente veredicto 👤 |

**Datapoint FPY de producción (2026-07-10):** 67% con briefs jamás vistos —
por debajo del objetivo 80%, con el ciclo de reparación rescatando el 100% de
las fallas. Es el baseline que la etapa MVP debe subir con datos reales.

---

## ETAPA MVP — Operación y validación (aprobada por el board 2026-07-10)

**Pregunta de cada tarea: ¿nos acerca a un cliente pagando? KPIs ejecutivos:
TTV (regla #25) + FPY + LV/LA. Motor de aprendizaje CONGELADO (regla #24).**

| Fase | Alcance | Criterio de salida |
|---|---|---|
| **M1 — Operación diaria** 🔨 | Jobs programados sobre la cola (plan del Planificador → producción → QA → cola de aprobación), gate L0 por tarjeta Teams (infra del bot existente), alertas de error, runbook. **Hecho 2026-07-10:** `daily_run.py` (idempotente por día, resumible tras crash sin re-planificar, CLI run/status/intervencion/hor, evento `ops.daily_run`), `bootstrap.py` (wiring de producción desde `marketing.yaml` del tenant — `objective_by_pillar` y `daily:` ahora son datos del tenant: deuda F3 saldada), KPI HOR implementado (las intervenciones se DECLARAN y castigan el índice), wrapper `run_marketing_daily.bat`. **Hecho 2026-07-10 (parte 2):** aprobación L0 (`approvals.py`: aprobar→scheduled / rechazar-con-motivo→qa_rejected, auditado, CLI aprobar/rechazar/pendientes) + notificaciones (`notify.py`: resumen diario con pendientes L0 y ALERTA si la corrida queda incompleta; notificar jamás tumba la corrida — fallo degradado a evento `ops.notify_failed`); destinatarios como datos del tenant. **Hecho 2026-07-14 (parte 3):** `preflight.py` con auto-reparación (npm ci automático si faltan deps de render — decisión del board 2026-07-13: el gap de deploy no puede depender de memoria humana; corre antes de `run` y jamás deja gastar con el entorno roto), **Runbook Operativo MVP** (`docs/runbook-mvp.md`: mapa operativo, deploy, incidentes §5.1–5.7, intervenciones, recuperación de desastres), y **tarjeta de aprobación L0 en Teams**: store compartido de decisiones (`marketing_l0_state.py`, Azure Table `marketingl0` — la PC y el bot no comparten disco) + 3 endpoints `/admin/marketing/l0-*` + tarjeta proactiva por el Data Bot (fallback Activities) con Aprobar/Rechazar-con-motivo, anti doble-tap y deciders autorizados; la PC aplica las decisiones al inicio de cada corrida (o `aplicar`) usando las MISMAS funciones que el CLI. **Hecho 2026-07-14 (parte 4):** bot DEPLOYADO a producción (55603c9, health OK) + puente configurado en la PC + **E2E completo validado con decisiones humanas reales** (tarjetas entregadas → aprobar/rechazar desde Teams → sync → `scheduled`/`qa_rejected` con auditoría `teams:<email>`); fallas probadas en vivo (bot caído → corrida sigue + evento `ops.l0_remote_failed`; sin config → skip; doble-tap y decider no autorizado → bloqueados; decisión de pieza desconocida → visible sin confirmarse en falso, runbook §5.8); incidente día 1: PC suspendida a las 07:30 → corrida manual declarada + fix raíz `StartWhenAvailable` en la schtask. Decisión de Daniel: **solo Mateo es aprobador L0** (`l0_approvers`); Daniel sigue en `notify_to`. **Falta:** 5 días del criterio (contador desde 2026-07-15) | 5 días hábiles seguidos generando el plan diario sin intervención manual (sin publicar aún) |
| **M2 — Visibilidad** | Dashboard esencial (`/media/*` patrón admin_api): cola, piezas, FPY/LV/LA, costos por pieza, decisiones del playbook; self-report semanal a gerencia (usa `render_report`) | Daniel puede responder "¿qué hizo el sistema esta semana y cuánto costó?" sin preguntarme |
| **M3 — Fase TikTok (5 pasos del board)** | Publisher tercero + credenciales seguras + cuenta de PRUEBA + monitoreo + validación E2E; historia asistida; luego cuenta real | 1 semana publicando en cuenta de prueba con FPY y ledger limpios → go/no-go del board para cuenta real |
| **M4 — TTV y onboarding** | Instrumentar TTV (evento install→primer resultado); empaquetar onboarding (intake del Brand Brain guiado); ensayo completo con tenant demo (Andex) midiendo TTV real | TTV medido y publicado; onboarding reproducible sin artesanía |

### Plan detallado M3 — integración TikTok (propuesto por el CTO 2026-07-14; se aprueba con el merge de este PR)

**Regla de oro (board):** NO se publica absolutamente nada hasta que el
sistema esté completamente estable y validado. Todo M3.0 se construye con la
publicación deshabilitada por TRES capas independientes: (1)
`publishing.enabled: false` en `marketing.yaml` del tenant, (2) regla dura
del charter que el kernel enforcea, (3) `NullPublisher` como backend por
defecto — el conector real solo se registra si flag + credenciales presentes.
Test de CI: intentar publicar con el flag apagado lanza y queda auditado.

**Realidades técnicas que fijan el diseño** (verificadas 2026-07-04):
una app de TikTok for Developers SIN auditar solo puede publicar en modo
privado/borrador (`SELF_ONLY`) — eso NO es una limitación para nosotros: es
exactamente el modo "cuenta de prueba" del paso 3 del board. La auditoría de
TikTok (para posts públicos) es un trámite externo de semanas y es EL cuello
del cronograma, no el código. Tokens: access ~24 h, refresh rotativo — el
conector maneja la rotación solo.

| ID | Tarea | Criterio de salida | Publica algo |
|---|---|---|---|
| M3.0a | Puerto `Publisher` (interfaz + registry + `NullPublisher`) integrado post-`scheduled`; la cola gana estado `published` (inalcanzable con el flag off) | Pieza aprobada queda `scheduled` para siempre con el flag off; test de CI del kill-switch | NO |
| M3.0b | `marketing/tiktok_connector.py`: OAuth2 con PKCE (CLI de autorización única → guarda tokens), store de tokens cifrado (DPAPI en la PC; Key Vault en SaaS), refresh con rotación automática, cliente para creator_info/publish/status con rate-limit y reintentos | Tests con HTTP fake: flujo completo, refresh, expiración, revocación; cero llamadas de red en pytest | NO |
| M3.0c | Simulacro E2E con `FakeTikTokPublisher`: scheduled → "publicada" → PostRef + polling de estado → métricas fake ingresadas al MetricsStore | El circuito completo corre en local sin tocar TikTok; ledger sin duplicados ante reintentos | NO (fake) |
| M3.0d | 👤 Registro de la app en TikTok for Developers (idealmente bajo la entidad VER-IA — registrarla bajo cuentas personales = deuda de migración; depende de separación corporativa F1) + solicitar scopes video.upload/video.publish | App creada, client_key/secret en el gestor de secretos | NO |
| M3.1 | Cuenta de PRUEBA: OAuth real, publicación **forzada a SELF_ONLY (privada)** — doble candado: modo sandbox de la app + validación del conector | 1 semana de soak publicando en privado con FPY y ledger limpios | Solo PRIVADO |
| M3.2 | Métricas reales (Display API) reemplazan al simulador — paga la deuda ALTA "aprendizaje validado solo contra simulador" | Analista corriendo sobre datos reales; primer reporte LV/LA real | Solo PRIVADO |
| M3.3 | 👤 Auditoría de TikTok aprobada → go/no-go del board → cuenta real | Acta del board; primer post público | SÍ (con acta) |

**M3.0a–c HECHOS 2026-07-14** (aprobación del board del mismo día): puerto
Publisher + kill-switch probado capa por capa + conector OAuth/PKCE con
tokens AES-GCM fail-closed multi-tenant + endpoints del bot
(`connect-start`/`callback`/`status`/`token`) + simulacro E2E con fake y
ciclo crash-safe anti doble-post. 25 tests nuevos, suite 567 en verde.
**Revisión técnica completa: `docs/revision-tecnica-m3-0.md`** — ratificar
antes de M3.0d. M3.0d/M3.3 son trámites externos que conviene iniciar YA
porque dominan el cronograma. Decisión de arquitectura: API directa de TikTok (el modo
pre-auditoría cubre la validación privada) con la app propia como activo del
producto VER-IA; terceros (Ayrshare/Buffer) quedan como fallback documentado
detrás del mismo puerto Publisher.

**Pospuesto a versión posterior (regla #24):** comunidad (F5), canal SEO (F6),
Profession Brain/CEO etapa 1 (F7), experimentos de costo (Haiku A/B, Batch API),
mejoras del motor de aprendizaje. **Acciones no técnicas en paralelo (👤):**
separación corporativa; ficha técnica del producto al Brand Brain; OKRs
numéricos del charter.

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
| F3 | 2026-07-10 | **Sin cambios a v0.1. ACTA APROBADA por el board.** Motor de aprendizaje completo y validado de forma falsable; FPY real 67% como baseline. **Promovidos a v1.0** ("extraídos de evidencia real, no de suposiciones"): (1) objetivos de negocio como dimensión de segmentación del conocimiento — generalizable a cualquier departamento; (2) validación de motores de decisión contra simuladores con ground-truth sembrado + control negativo como patrón estándar; (3) derivar parámetros de los datos, no pasarlos. **Board pivota a VALOR (reglas #24-26): motor congelado, etapa MVP, KPIs TTV y HOR, reporte CTO→CEO con 4 preguntas.** | demo F3.7 + revisión ejecutiva |
| F2 | 2026-07-10 | **Sin cambios a v0.1.** Board aprobó formalmente (4 condiciones cumplidas). **Promovido a v1.0:** (1) los pipelines largos se construyen como colas persistentes y resumibles POR DEFECTO; (2) todo revisor LLM usa contrato blockers/mejoras + pruebas adversariales periódicas de calibración. Directrices nuevas para F3: métricas con propósito (#16), Analista conservador (#17), KPI Learning Velocity (#18). | Demo E2E por la cola (FPY 1.0, $0.115/2 piezas) |
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

### Creada en F3

| Deuda | Impacto | Prioridad | Resolver en |
|---|---|---|---|
| Pesos de leads/sales con PROXIES (saves/comments) hasta el contrato `LeadOutcome` | El "valor comercial" es aproximado | Media | Fase TikTok (métricas reales) + departamento Comercial (conversión real) |
| `objective_by_pillar` vive como parámetro, no como dato del tenant | Onboarding manual | Media | M1 (mover a `tenants/<slug>/marketing.yaml`) |
| FPY real 67% < objetivo 80% (briefs nuevos) | Calidad de primera pasada | Media | Etapa MVP: analizar los 2 rechazos de 1er intento y ajustar prompt/gate con evidencia |
| Todo el aprendizaje validado SOLO contra simulador | El riesgo #1 del proyecto | **Alta** | M3 (primeras métricas reales) — no hay atajo honesto |

### Creada en F2

| Deuda | Impacto | Prioridad | Resolver en |
|---|---|---|---|
| La sonda de calibración usa API real y es manual (no corre en CI) | Riesgo de olvidarla tras un cambio de rúbrica | Media | F4 (job opcional en CI con secret, o checklist de PR) |
| Duración en borrador = estimación a 2.6 palabras/s (la real la mide el TTS) | Desvío estimado-vs-real posible en guiones atípicos | Baja | Vigilar con telemetría (`speech_ms` vs estimado); ajustar la constante con datos |
| Cola single-process (sin lease multi-worker) — coherente con el "1 worker deliberado" de la plataforma | Concurrencia futura | Baja | H2 (junto con Postgres) |
| `render/public/` sigue sin limpieza (0.6 GB acumulados; deuda F1 arrastrada) | Disco | Media | F3 (limpieza post-gate-final en el runner) |
| FPY medido con los mismos 10 briefs de calibración | El 100% no es todavía un FPY de producción | Media | F3 (el Planificador genera briefs variados; FPY continuo) |

### Creada en F1

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
