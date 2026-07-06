# ROADMAP — Marketing Brain sobre VER-OS

**Fuente única de verdad del progreso del proyecto.** Se actualiza en cada PR que
avance una tarea. Línea base aprobada (2026-07-06): `PROPUESTA_VER_OS.md` v0.1 +
`PROPUESTA_TIKTOK_MEDIA_MANAGER.md` v3.

**Estados:** ⬜ pendiente · 🔨 en curso · ✅ hecho · ⛔ bloqueado · 👤 acción humana

**Regla de cierre de fase (4 condiciones):** (1) software ejecutable y demostrable;
(2) pruebas automatizadas + documentación mínima; (3) alcance cerrado por completo;
(4) revisión técnica: ¿lo aprendido exige ajustar VER-OS v0.1 antes de seguir?
El registro de esas revisiones vive al final de este archivo.

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
| **Pilares de contenido** (propuesta: educación sostenibilidad, producto en uso, tips food-service, detrás de cámaras, tendencias eco EC) | 👤 confirmar — bloquea F1 |
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
| F0.10 | Integración de metering con `llm_usage.py` (las llamadas LLM de un dept se reflejan en su meter) | F0.4 | Test de doble registro (llm_usage + meter del dept) | ⬜ |
| F0.11 | Doc mínima `docs/ver-os-kernel.md` (qué hay, cómo usarlo, qué es convención vs invariante) | F0.1–F0.9 | Existe y refleja el código real | ✅ |
| F0.12 | **Revisión técnica de fase** con el board: ¿ajustar VER-OS v0.1? | todo F0 | Acta en §Revisiones de este archivo | ⬜ 👤 |

## Fase 1 — Producción de contenido

**Objetivo:** pipeline guion→TTS→render→QA + carruseles, sin publicar.
**Bloqueada por:** pilares de contenido (👤).

| ID | Tarea | Depende de | Criterio de aceptación | Estado |
|---|---|---|---|---|
| F1.1 | Modelos `ContentPackage`/`PlatformRendition` + `PlatformProfile` YAML (TikTok) | F0 | Validación pydantic estricta (patrón `core/config/schema.py`) | ⬜ |
| F1.2 | Guionista (Claude Sonnet, JSON validado, registra en `llm_usage`+meter) | F1.1 | 10 guiones válidos consecutivos sin intervención | ⬜ |
| F1.3 | TTS Azure neural es-EC con word boundaries persistidos | F1.1 | Audio + timestamps por palabra en el package | ⬜ |
| F1.4 | B-roll Pexels/Pixabay por keywords del guion (cache local) | F1.2 | Assets descargados y atribuidos en el package | ⬜ |
| F1.5 | Render Remotion 1080×1920 + subtítulos karaoke desde timestamps TTS + portada | F1.3, F1.4 | Video H.264 válido; QA técnico automático (duración/res/loudness) pasa | ⬜ |
| F1.6 | Carruseles: plantillas HTML → PNG 1080×1920 (Playwright local) | F1.1 | 5–10 slides de marca desde un package | ⬜ |
| F1.7 | Gate de calidad: revisor Claude con rúbrica de marca + claims prohibidos del charter | F1.2 | Pieza con claim vetado → rechazada con razón | ⬜ |
| F1.8 | Demo: 10 piezas (8 videos + 2 carruseles) para aprobación de gerencia | F1.5–F1.7 | 👤 gerencia aprueba calidad | ⬜ |
| F1.9 | Revisión técnica de fase | todo F1 | Acta | ⬜ 👤 |

## Fase 2 — Publicación L0 (TikTok)

**Bloqueada por:** cuenta Zernia/Buffer + cuenta TikTok de prueba (👤), OKRs (👤).

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
| F0 | — | pendiente | — |
