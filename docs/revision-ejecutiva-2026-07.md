# Revisión Ejecutiva — VER-IA Marketing Brain sobre VER-OS

**Fecha:** 2026-07-10 · **Preparada para:** board VER-IA (formato comité directivo)
**Estado del código:** rama `feat/veros-f0`, 500 tests, ~40 commits desde el 2026-07-06.
**Inversión total del período:** ~5 días de ingeniería + <$5 de APIs.

---

## 1. Qué ya está RESUELTO (con evidencia, no con promesas)

**a) Producción de contenido de nivel publicable, a costo marginal despreciable.**
Pipeline completo guion→voz es-EC→b-roll→render con subtítulos karaoke, videos
20–30 s y carruseles de marca. **$0.02–0.05 por pieza terminada** (74% de tokens
desde cache), ~25 s de generación de copy, render local $0. Demostrado con
~30 piezas reales producidas.

**b) Control de calidad de nivel director creativo, automático y barato.**
Gate en dos capas (deterministas $0 + rúbrica LLM calibrada con contrato
blockers/mejoras) + ciclo de reparación con feedback. Historia medida y
falsable: FPY 0%→10%→100% en 3 iteraciones de calibración, verificado con
sonda adversarial (5/5) que quedó como test de regresión permanente. El QA
caza claims sin sustento, datos inventados, typos en hashtags y violaciones
de marca — cosas que a un revisor humano se le escapan un viernes.

**c) Un motor de aprendizaje gobernado — el corazón del producto.**
Métricas con propósito (dato sin pregunta de negocio no se almacena), scoring
por objetivo comercial, veredictos por estadística conservadora con 4 estados
("no sé" es un resultado computado), separación Analista-propone /
Knowledge-Manager-decide / Playbook-versionado-con-madurez, exploración
dirigida por huecos de evidencia, y KPIs LV+LA. **Validado de forma falsable**:
descubre sesgos sembrados en un simulador, no inventa sesgos inexistentes, y
LA castiga el conocimiento que no sobrevive a más datos.

**d) Fundaciones de plataforma (VER-OS).**
Kernel con los 12 componentes (tenancy absoluto, journal inmutable, metering
grado facturación, capacidades explícitas), cola de producción crash-safe, y
gobernanza operando de verdad: 23 reglas del board institucionalizadas, actas
de fase, deuda técnica explícita.

## 2. Qué sigue siendo un RIESGO

1. **Cero datos reales.** Todo el aprendizaje está validado contra simulador.
   El mercado real trae ruido no estacionario, algoritmos cambiantes y efectos
   que ningún simulador reproduce. Mitigación existente: diseño conservador +
   LA detectará conocimiento que no sobrevive. Pero es LA incógnita del proyecto.
2. **Distancia a operación 24/7.** No hay scheduler diario corriendo, ni
   dashboard, y el runner vive en la PC de Mateo (se suspende, mata procesos —
   lo sufrimos en el lote F1.8). La cola persistente lo amortigua; no lo elimina.
3. **Separación corporativa incompleta.** Repo, Azure e IP siguen en el entorno
   del cliente #1. No se puede vender lo que legalmente vive donde el cliente.
   (Ya trackeado como acción de Daniel; sigue abierto.)
4. **La última milla no recorrida:** publicar. La integración TikTok está
   diseñada (terceros auditados) pero diferida por decisión del board — correcta,
   pero significa que el flujo alcance→métrica→venta real no está ejercitado.
5. **Concentración en terceros:** políticas de TikTok, pricing de Anthropic,
   catálogo de Pexels. Puertos y mitigaciones diseñados, no ejercitados.

## 3. Qué falta para un MVP COMERCIAL
*(= el tenant #1 publicando a diario con supervisión L0/L1)*

| Falta | Esfuerzo estimado |
|---|---|
| Fase TikTok (publisher tercero + credenciales + cuenta de prueba + monitoreo + validación — el plan de 5 pasos del board) | 2–3 semanas (diseño ya hecho) |
| Orquestación diaria (jobs sobre la cola: plan→producción→QA→cola de publicación) + gate de aprobación L0 por Teams | 1 semana |
| Dashboard mínimo + self-report semanal (F4 recortado a lo esencial) | 1 semana |
| Ficha técnica de producto en el Brand Brain (👤 acción Biodegradables) | horas del cliente |
| Música de biblioteca + pulido visual (nivel "premium") | 1 semana, opcional para MVP |

**Total: ~4–6 semanas** hasta contenido publicándose a diario con calidad gobernada.

## 4. Qué falta para un PRODUCTO SaaS
*(= cobrar a N clientes)*

Todo lo del MVP más: separación corporativa (bloqueante legal), onboarding
como producto (wizard del Brand Brain — hoy es artesanal), Postgres+RLS y Key
Vault (los puertos existen), render en Azure Container Apps, billing sobre el
metering (los datos ya se capturan al centavo), panel de cliente, SLOs/soporte,
y lo legal-comercial (ToS, DPA, pricing). **Es el horizonte H2 del plan VER-OS:
~2–3 meses adicionales tras el MVP**, sin re-arquitectura — esa fue la apuesta
de diseño y hasta ahora se sostiene.

## 5. Las TRES mayores ventajas competitivas construidas

1. **Conocimiento auditable y gobernado.** Ningún competidor "IA que aprende por
   prompts" puede responder: *¿por qué publicaste esto, qué evidencia tenías,
   qué aprendiste y cuánto de lo aprendido sigue siendo cierto?* Nosotros sí —
   con journal, madurez de reglas, reversibilidad y LV+LA. Para un cliente
   empresarial, eso es la diferencia entre magia y un empleado confiable.
2. **La calidad es un sistema, no una persona.** Gate + reparación + FPY +
   sondas de calibración = QA de director creativo a ~$0.01/pieza, que escala a
   cientos de marcas sin contratar revisores. Y es vendible por separado.
3. **Economía sublineal medida + arquitectura de puertos.** Costo por pieza
   conocido al centavo, cache al 74%, telemetría por etapa; multi-tenant,
   multi-canal y multi-LLM por diseño. El margen y la extensibilidad son
   estructurales, no aspiracionales.

## 6. Las TRES mayores debilidades actuales

1. **El aprendizaje no ha tocado la realidad.** Un motor impecable contra
   simulador puede humillarse contra el algoritmo de TikTok. Hasta que LA se
   mida con datos reales, el "sistema que aprende" es una hipótesis bien
   construida — no un hecho.
2. **No operamos, demostramos.** Cada lote hasta hoy lo lancé yo a mano. Sin
   scheduler, dashboard y runner confiable, no hay producto — hay un prototipo
   excelente.
3. **Dependencias de terceros no ejercitadas bajo estrés** (TikTok, Anthropic,
   Pexels) y un solo tenant real: el multi-tenant está probado a nivel kernel,
   no a nivel negocio.

## 7. Recomendación del CTO

**Entrar en estabilización y camino al MVP. Congelar nuevas capacidades de
aprendizaje hasta tener datos reales.** El motor ya es más sofisticado que los
datos que lo alimentan — construirle más músculo sería optimizar contra un
simulador. Secuencia propuesta: cerrar F3 (F3.7 ciclo E2E con FPY real de
briefs nuevos, F3.9 limpieza, acta F3.10) → F4 mínimo operativo (orquestación
diaria + dashboard esencial + self-report) → fase TikTok con cuenta de prueba
según el plan de 5 pasos del board → primera semana de publicación L0
supervisada. En paralelo (acciones no técnicas): separación corporativa y
ficha técnica del producto. Con eso, la pregunta "¿esto aprende de verdad?"
se responde con el KPI más honesto que existe: LA contra el mundo real.
