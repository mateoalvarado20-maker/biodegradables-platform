# Revisión F1.8 + Retrospectiva de Fase 1

**Fecha:** 2026-07-09 · **Lote:** 11 piezas reales (8 videos + 2 carruseles + 1 saboteada)
por el pipeline completo con gate real. Datos crudos: journal del departamento
(`~/.ver-os/state-demo-f18`), guiones en `marketing/render/public/*/props.json`,
resultados en `~/.ver-os/demo-f18/`.

---

## 1. El hallazgo central (léase primero)

**El gate rechazó las 10 piezas del lote (0 aprobadas).** Y esa es la mejor noticia
del experimento, por dos razones:

1. **Los rechazos NO son por calidad de producción.** Voz, render, subtítulos
   karaoke, portadas y slides están a nivel publicable. Los rechazos son por
   **copy**: emojis contra la regla de marca, claims técnicos sin sustento,
   CTAs repetidos o muertos, 2 duraciones cortas y 1 dato inventado.
2. **El revisor está haciendo trabajo de director creativo de agencia.** Cazó un
   typo en un hashtag (`bioedegradables`), un CTA de WhatsApp sin número (canal
   que muere en la pieza), una comparación implícita con competidores (prohibida
   por marca), una insinuación de muestras gratis (práctica que el contexto veta)
   y la pieza saboteada con "cero impacto ambiental" (rechazo determinista, $0,
   sin pasar por el LLM — **criterio F1.7 verificado**).

La conclusión honesta: construimos un generador bueno y un crítico excelente,
pero **no existe el ciclo entre ellos**. Las piezas rechazadas mueren en vez de
repararse. Ese ciclo de reparación es la pieza faltante #1 (ver §6).

## 2. Calidad del contenido, pieza por pieza

| # | Pieza | Score | Veredicto | Por qué |
|---|---|---|---|---|
| 9 | Carrusel checklist proveedor | **81** | ✋ casi | CTA "WhatsApp" sin número (canal muerto); claim "cierre hermético" sin ficha técnica |
| 10 | Carrusel 4 mitos | **78** | ✋ casi | CTA "síguenos" débil para B2B; 2 claims técnicos de compostaje sin respaldo |
| 5 | Error al pedir empaques (video) | **74** | ✋ casi | Emojis; CTA 3×; insinúa muestras gratis (vetado); typo en hashtag |
| 1 | Compostable vs biodegradable | 72 | ✗ | Emojis; CTA 3×; "compostable es el estándar más alto" sin respaldo |
| 2 | Mito: se deshace en días | 72 | ✗ | Emojis; copy duplicado guion↔caption; CTA a canal no definido |
| 6 | 3 señales (video) | 72 | ✗ | Emojis; "sin olor/resistente al calor" como absolutos; compara con competidores |
| 3 | Sorbetes 4 horas (video) | 61 | ✗ | **Claim central "4 horas" inventado** — riesgo legal directo |
| 8 | Tendencia Quito (video) | 41 | ✗ | **Hook con dato inventado** (restaurantes que descuentan) — desinformación |
| 7 | Detrás de cámaras bodega | 0 | ✗ det. | Duración 16 s < estándar 20-30 s |
| 11 | Saboteada (F1.7) | 0 | ✗ det. | Claim prohibido del charter + duración — **rechazo correcto sin gastar LLM** |
| 4 | Mesa de evento | — | ⚠ render | Falló 2× por clips UHD (fix aplicado; 3er intento en curso) |

**Mejores 3:** carrusel checklist (9), carrusel mitos (10), error-al-pedir (5).
**Peores 3:** tendencia Quito (8, dato inventado — la más peligrosa), saboteada (11,
a propósito), sorbetes (3, claim específico sin fuente).

**Patrones observados:**
1. **Emojis sistemáticos** (7/10 piezas): la regla vive en el contexto de marca
   pero el guionista no la recibe como instrucción dura. Fix barato: regla al
   system prompt + check determinista (regex) en el gate.
2. **Especificidad inventada**: cuando el contexto no da un dato, el guionista
   rellena con precisión plausible ("4 horas", "sin olor", "cierre hermético").
   Es el riesgo #1 de marca. El gate lo caza — pero mejor no generarlo.
3. **CTA repetido 3×** (caption + escena final + overlay): defecto de ensamblaje,
   no de creatividad.
4. **Storytelling queda corto**: los formatos narrativos (detrás de cámaras,
   demo) tienden a <20 s; el prompt fuerza máximo pero no mínimo con dureza.
5. **Hook "dato-sorprendente" = fábrica de datos inventados** sin una fuente que
   citar. Restringirlo a datos con fuente (news_brief, catálogo).
6. **Carruseles > videos** en score (81/78 vs 41-74): menos superficies de error
   (sin voz, sin duración, copy leído y editado como unidad).

## 3. Evaluación de negocio

- **Alcance:** carrusel de mitos (10) y los educativos (1, 2) — formatos
  compartibles/guardables; el checklist (9) es el clásico "guardar para después".
- **Ventas:** checklist (9) y error-al-pedir (5) — hablan al dolor del comprador
  B2B y el CTA natural es la asesoría gratis (diferenciador real). El de sorbetes
  (3) sería fuerte en ventas **si el claim se valida**: pedir a Biodegradables la
  ficha técnica real convierte un riesgo legal en el mejor argumento de venta.
- **Confianza de marca:** detrás de cámaras (7) y los educativos con matices
  honestos (2 dice "en un relleno sanitario casi nada se degrada" — honestidad
  que construye credibilidad).

## 4. Evaluación del sistema

**Mejor de lo esperado:** el gate (nivel director creativo); TTS con word
boundaries (sync perfecto, es-EC natural); costo — $0.4774 el lote completo
(~13 guiones + 8 revisiones + reintentos ≈ **$0.024/pieza guion+QA**), con 74%
de tokens de entrada servidos de cache (11.241 de 15.159).

**Peor de lo esperado:** el render — 6-12 min/pieza en la PC, 2 fallos por clips
UHD que Chrome no decodifica (fix: cap de resolución en `_pick_file`; refactor
OffthreadVideo pendiente), y sensibilidad total a suspensión de la PC (una pieza
"tardó" 39 h porque la máquina durmió). El copy del guionista necesitó más
reglas de las previstas (emojis, duración mínima).

**Cuello de botella actual:** el render en CPU local — 85-90% del tiempo por pieza.

**Mayor riesgo técnico para F2:** operar publicación programada sobre un runner
frágil (PC que se suspende, procesos que mueren) **sin una cola persistente y
resumible**. El lote lo demostró: 3 relanzamientos manuales. F2 debe empezar por
la cola (los packages ya nacen con estado y ledger — falta materializarla).

## 5. Retrospectiva formal de Fase 1

**¿Qué aprendimos sobre VER-OS?** Los invariantes no estorbaron ni una vez — al
contrario: el journal con razones convirtió un lote fallido en una auditoría
legible, el metering convirtió "se siente lento/caro" en números, y la
telemetría (directriz #14) pagó su costo tres veces (bug del cache, UHD,
duraciones cortas). La tesis "estándar extraído de la implementación" se
confirma: nada del kernel necesitó cambio, y lo que sí aprendimos (separación
registro/enforcement, claim-once) ya estaba en el backlog de v1.0.

**Decisiones que demostraron ser correctas:** backends inyectables en todas las
etapas (permitió 74 tests sin red y servicios reales en demos); datos del cliente
en `tenants/` (claims, voz, pilares, contexto — el gate los consume sin código);
reutilizar Remotion para carruseles; hipótesis de negocio obligatoria (cada pieza
del lote sabe qué experimento es); gate en dos capas con lo determinista primero.

**Qué cambiaría si empezáramos hoy:** (1) **persistencia de ContentPackages desde
F0** — vivieron solo en memoria y la auditoría tuvo que reconstruirlos desde los
props del render; (2) **pipeline como cola resumible** desde el inicio, no script
encadenado; (3) OffthreadVideo desde el día 1 (el `<Video>` del browser es el
origen de los 2 fallos UHD).

**¿Sobreingeniería?** Poca y consciente: la escalera de autonomía y los contratos
inter-departamento aún no tienen consumidores reales — son inversión de invariante,
no código muerto, y no costaron mantenimiento. El validador de contratos sigue
mínimo (regla #6 vigilada).

**Refactors futuros:** extraer el LLM-call duplicado guionista/gate a un helper
del dominio; `<Video>` → `OffthreadVideo` (+duración del clip desde la API de
Pexels); limpieza automática de `render/public/` (deuda F1); orquestación F2.0.

**Oportunidades de producto descubiertas:**
1. **El gate como producto standalone** — "QA de marca para contenido" (rúbrica +
   claims prohibidos como datos del cliente) es vendible por sí solo a empresas
   que ya producen contenido con agencias o freelancers.
2. **Motor de carruseles/stills** reutilizable por otros departamentos (reportes
   visuales del Comercial, por ejemplo).
3. **Costo por pieza como dashboard de ROI** para el cliente (la telemetría ya lo
   produce).
4. **El Brand Brain con ficha técnica de producto**: el patrón "claims solo desde
   datos validados" es exactamente compliance-as-data — diferenciador SaaS.

## 6. Veredicto de publicabilidad (la pregunta directa)

**¿El contenido está al nivel de lo que una empresa real publicaría sin vergüenza
y sin edición manual? NO — todavía.** Y la evidencia es nuestra, no mía: 0/10
pasaron nuestro propio estándar. Matices importantes: (a) la producción
audiovisual SÍ está a nivel; (b) 3 piezas están a un retoque de copy de
publicarse (81/78/74); (c) el sistema *sabe* por qué falla cada pieza, con
razones específicas — eso es lo que hace el problema cerrable.

**Qué falta exactamente (en orden de impacto):**
1. **Ciclo de reparación gate→guionista** (F2.0): re-generar con las razones del
   rechazo como feedback, máx 2 intentos. Los defectos dominantes (emojis, CTA
   repetido, duración) son mecánicamente corregibles — estimo 6-8/10 auto-aprobadas
   con solo esto.
2. **Reglas de estilo duras en el prompt del guionista** + checks deterministas
   baratos en el gate (regex de emojis, typos de hashtags contra lista blanca,
   duración mínima ya cubierta).
3. **Ficha técnica real del producto en el Brand Brain** (👤 acción Biodegradables):
   resistencia térmica, tiempos, certificaciones. Convierte los claims inventados
   —que son buen marketing mal sustentado— en los mejores argumentos del catálogo.
4. **Hook "dato-sorprendente" solo con fuente citable** (integrar `news_brief`).
5. Para el nivel "editado a mano premium" (no bloqueante): música de biblioteca,
   variedad visual (2 clips/escena o zoom sutil), y validación humana viendo 2-3
   videos completos — esta revisión evaluó guiones, frames y veredictos; el
   motion completo lo deben ver ojos humanos al menos una vez.

**Recomendación:** cerrar F1 como "pipeline completo + estándar de calidad
funcionando", y arrancar F2 con F2.0 = ciclo de reparación + cola persistente,
ANTES del publisher. Publicar sin eso sería publicar lo que nuestro propio
estándar rechaza.
