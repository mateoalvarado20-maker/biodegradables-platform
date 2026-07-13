# VER-OS v0.1 — El sistema operativo organizacional de VER-IA (borrador normativo)

**Fecha:** 2026-07-06 · **Estado:** borrador para aprobación · **Madurez:** v0.1 (ver §1.2)
**Relación con otros docs:** `PROPUESTA_TIKTOK_MEDIA_MANAGER.md` v3 define el Marketing
Brain, que pasa a ser la **implementación de referencia** de este estándar.
`PROPUESTA_ARQUITECTURA_MULTIEMPRESA.md` definió el modelo de tenants; VER-OS lo asume.

---

## 0. Qué es VER-OS (y qué no es)

**Es:** el estándar que define qué componentes debe tener cualquier departamento
autónomo de VER-IA — presente, futuro o de terceros — y cómo colaboran entre sí.
Un contrato entre la plataforma y quien construya sobre ella (incluidos nosotros).

**No es:** un framework de agentes genérico, un orquestador de LLMs, ni software que
se construya "aparte". VER-OS se materializa como el `org/kernel` + `org/contracts` +
`org/events` que el plan de Marketing ya construye en F0; este documento fija el
estándar que ese código implementa.

---

## 1. Filosofía de diseño (léase antes de discutir cualquier detalle)

### 1.1 Los estándares se extraen, no se inventan
El riesgo #1 de este paso es el clásico de plataformas: **especificar un OS completo
con cero implementaciones produce un estándar equivocado** (second-system effect).
La política de VER-OS es explícita:

- **v0.1 (este doc):** fija SOLO los **invariantes de carga** — decisiones
  irreversibles o carísimas de cambiar después (§1.3). Todo lo demás es convención
  provisional.
- **v1.0:** se publica DESPUÉS de que Marketing (implementación de referencia) opere
  un trimestre. Las convenciones que sobrevivan al contacto con la realidad se
  promueven a norma; las que no, se corrigen sin coste de migración (había 1 solo
  departamento).
- Ningún segundo departamento se construye sobre v0.1: se construye sobre v1.0.

### 1.2 Niveles normativos
Cada regla del estándar lleva etiqueta: **[INVARIANTE]** (no cambiará; el marketplace
puede depender de ella), **[NORMA]** (estable, cambia solo con versión mayor del
estándar), **[CONVENCIÓN]** (provisional hasta v1.0).

### 1.3 Los invariantes de carga (lo que se congela HOY)
1. **[INVARIANTE] El tenant es la frontera absoluta.** Ningún dato, evento, contrato
   ni aprendizaje identificable cruza empresas. Lo compartido entre tenants es solo
   conocimiento agregado y anonimizado (Profession Brains), con opt-out por tenant.
2. **[INVARIANTE] Todo departamento es un principal con capacidades explícitas.**
   Sin autoridad ambiente: lo que no está declarado en su manifest y otorgado por el
   tenant, no existe para él.
3. **[INVARIANTE] Los datos de dominio pertenecen a su departamento.** El acceso
   entre departamentos es SOLO vía contratos versionados; nunca lectura directa.
4. **[INVARIANTE] Toda acción autónoma es auditable:** decision journal append-only
   correlacionado con eventos, legible por el board del tenant.
5. **[INVARIANTE] El board es humano.** Misión, presupuesto global, OKRs y veto son
   humanos en todo tenant. La autonomía (L0→L3) se gana por track record y es
   revocable en un comando.
6. **[INVARIANTE] Sobre de evento estándar** (§3.8) y **contratos con semver**: el
   formato del envelope y la política de compatibilidad no cambian jamás de forma
   breaking (se versionan aditivamente).
7. **[INVARIANTE] Metering de costos por (tenant, departamento, unidad de trabajo)**
   desde el primer día — el billing del SaaS se construye sobre esto (§3.10).
8. **[INVARIANTE] Neutralidad de modelo LLM:** ningún componente normativo depende de
   un proveedor de modelo. Los brains son artefactos portables (texto estructurado +
   datos), no prompts acoplados a un modelo.

---

## 2. Modelo conceptual

```
PLATAFORMA VER-IA (control plane global: registry, billing, conformance, marketplace*)
 └── TENANT (= empresa cliente; aislamiento absoluto)
      ├── BOARD (humanos: misión, presupuesto, OKRs, veto, nivel de autonomía)
      ├── CEO AGENT (opcional; etapa 1 "jefe de gabinete" → etapa 2 con ≥2 deptos)
      ├── DEPARTAMENTO 1..n (= instancia de un paquete VER-OS sobre el kernel)
      │     └── agentes de dominio + puertos + brain + journal
      ├── org_events (bus append-only del tenant)
      └── org_contracts (instancias de contratos activos entre sus departamentos)
(*) marketplace: horizonte 3, ver §5.
```

Un **departamento** es la unidad de despliegue, permiso, costo y rendición de cuentas.
Un **paquete** es la definición instalable de un departamento (código + manifest).
Un **conector** es un adapter a sistema externo (TikTok, Contifico, HubSpot…) — también
empaquetable y con manifest propio, reutilizable por varios departamentos.

---

## 3. Los 12 componentes obligatorios de un departamento

Todo departamento VER-OS DEBE implementar los 12. El `org/kernel` provee la
implementación por defecto de 10 de ellos (dominio solo aporta contratos y métricas
de dominio); un paquete de terceros puede sustituir implementaciones SOLO donde el
nivel de confianza se lo permita (§5.2).

| # | Componente | Norma mínima |
|---|---|---|
| 1 | **Identidad** [INVARIANTE] | `dept_id` único por tenant + identidad del paquete (publisher, versión, firma). Es un principal con credenciales propias emitidas por el tenant, de mínimo privilegio, rotables y revocables. |
| 2 | **Memoria** [NORMA] | Brain estándar: contexto de dominio + playbook con procedencia (regla → experimentos → confianza → vigencia) + decision journal + experiment registry. Exportable/importable (portabilidad = requisito SaaS: el cliente es dueño de su brain y se lo lleva si se va). Materializado también como Markdown legible. |
| 3 | **Autonomía** [NORMA] | Escalera L0 (supervisado) → L1 (auto con gate) → L2 (autónomo operativo) → L3 (autónomo estratégico). Transiciones por criterios medibles registrados en el journal; downgrade automático ante incidentes; default seguro ante silencio humano (no actuar). |
| 4 | **Permisos** [INVARIANTE] | Capacidades declaradas en el manifest (`capabilities:`), otorgadas explícitamente por el admin del tenant en la instalación, enforced por el kernel en runtime (no por convención de código). |
| 5 | **Auditoría** [INVARIANTE] | Decision journal append-only: decisión, contexto usado (refs, no copias), alternativas, regla de playbook aplicada, `correlation_id`. Legible por board y CEO Agent; inmutable. |
| 6 | **Observabilidad** [NORMA] | Health estándar (`/health` del dept: colas, últimos jobs, errores, latencias), logs estructurados con `tenant_id`+`dept_id`+`correlation_id`, y heartbeat al control plane. Sin esto el control plane de 100 tenants es inoperable. |
| 7 | **Contratos** [INVARIANTE] | Declarados en manifest (`provides:` / `consumes:`), esquemas JSON Schema en registry con semver; payloads de mínimo necesario; breaking = versión nueva conviviendo con la anterior. |
| 8 | **Eventos** [INVARIANTE] | Envelope estándar: `{event_id, tenant_id, dept_id, type, schema_version, occurred_at, correlation_id, payload}`. Emisión de eventos de ciclo de vida obligatoria; consumo idempotente por `event_id`. |
| 9 | **Métricas** [NORMA] | Scorecard estándar contra OKRs del charter (para que board y CEO comparen departamentos heterogéneos) + métricas de dominio libres. Self-report periódico obligatorio en formato común. |
| 10 | **Costos** [INVARIANTE] | Metering por unidad de trabajo (tokens LLM, renders, posts, llamadas a conectores) etiquetado (tenant, dept, unidad, timestamp). Presupuesto en el charter; el kernel corta al agotarlo (regla dura, no advertencia). Es la base del billing SaaS y del margen por cliente. |
| 11 | **Seguridad** [INVARIANTE] | Secretos SOLO vía el secret store de la plataforma (nunca en código/manifest/brain); jerarquía de reglas duras (plataforma > tenant/board > departamento — las de arriba no son editables por lo de abajo); tenant isolation en toda query (enforced por el storage layer, no por disciplina). |
| 12 | **Ciclo de vida** [NORMA] | Estados: `proposed → installed → onboarding → active(L0..L3) → paused → retiring → retired`. Instalación = otorgar capacidades + charter firmado por board. Retiro = **handover obligatorio**: el brain exportado y las obligaciones contractuales transferidas o cerradas formalmente (un departamento no puede "desaparecer" dejando contratos colgados). |

## 3.1 El manifest (formato de paquete)

```yaml
# verops.yaml — manifest de paquete de departamento (o conector)
verops: "0.1"
package: { name: marketing-brain, version: 1.0.0, publisher: ver-ia, kind: department }
trust_tier: first_party        # first_party | partner | community  (§5.2)
capabilities:                  # lo que PIDE; el tenant decide qué otorga
  - connectors: [tiktok_publisher, wordpress]
  - llm: { budget_usd_month: 60 }
  - notify: [teams_cards, email_reports]
contracts:
  provides: [LeadHandoff@1, WeeklyDeptReport@1]
  consumes: [LeadOutcome@1, BudgetEnvelope@1, VoiceOfCustomer@1?]   # ? = opcional
events:
  emits: [content.published@1, lead.captured@1, escalation.raised@1]
  subscribes: [org.budget_updated@1]
autonomy: { max_level: L3, default: L0 }
compliance: { data_categories: [marketing_content, public_metrics], pii: none }
```

[NORMA] El formato exacto es convención hasta v1.0; **la existencia del manifest con
estas secciones es invariante** (el marketplace entero depende de poder leerlo).

---

## 4. Lente SaaS: decisiones internas que bloquearían el producto comercial

Auditoría honesta de lo decidido en v1–v3 y del estado actual de la plataforma.
"Cuándo" indica el momento en que la decisión interna se vuelve bloqueante:

| # | Decisión actual (válida para tenant #1) | Por qué bloquea el SaaS | Alternativa | Cuándo corregir |
|---|---|---|---|---|
| 1 | **Repo, Azure e IP viven en la infraestructura del cliente #1 y la PC de Mateo** (ya flaggeado en el pivot VER-IA) | No se puede vender lo que legalmente vive donde el cliente; riesgo de IP irresoluble a posteriori | Repo/tenant Azure propios de VER-IA + acuerdo de IP con Biodegradables sobre lo ya construido; el código VER-OS nace en el repo de VER-IA | **ANTES de F0 de VER-OS** — es la única pre-condición legal, no técnica |
| 2 | Render/worker en la PC de Mateo | SPOF inaceptable con clientes pagando SLA | Azure Container Apps Jobs desde F3 (adelantado desde F7) | Antes del primer cliente externo |
| 3 | Secretos en env vars User de Windows | Ni rotación ni aislamiento por tenant | Azure Key Vault por tenant, kernel los inyecta por capability | Antes del primer cliente externo |
| 4 | SQLite por tenant | Operativamente inviable a 100 tenants (backups, migraciones, RLS) | Puerto de storage en el kernel desde F0; swap a PostgreSQL con row-level security en H2 | H2 (≥ ~5 tenants) |
| 5 | Publisher Zernio free / cuentas ad-hoc | Un SaaS no puede depender del free tier de otra startup; ToS de reventa dudosos | App TikTok propia auditada (ya en plan F4) como estrategia, terceros como bootstrap; contrato enterprise (Ayrshare) como puente si hay clientes antes de la auditoría | H2 |
| 6 | Prompts y contenido es-EC hardcodeados | Mercado limitado a un dialecto | Brains con campo de locale; plantillas separadas de lógica (el kernel ya lo induce) | H2, barato si se respeta desde F0 |
| 7 | Costos "estimados", no medidos | Sin metering no hay pricing defendible ni margen conocido | Componente #10 (metering) como invariante desde F0 | F0 (por eso es invariante) |
| 8 | Acoplamiento a Claude/Anthropic en agentes | Riesgo de proveedor y de pricing para un SaaS | Invariante #8: puerto LLM en el kernel; brains portables; modelos elegibles por (tenant, tarea) | F0 (puerto), H2 (multi-proveedor real) |
| 9 | Aprobaciones vía Teams del tenant #1 | Clientes sin Microsoft 365 quedan fuera | Puerto de notificación/aprobación (Teams hoy; email/WhatsApp/web después) | H2 |
| 10 | Onboarding artesanal (1 h con Mateo) | No escala a 100 | El intake se vuelve producto: wizard que construye el Brand Brain + charter | H2 |

**El punto 1 es el único que escalo como bloqueante inmediato:** es barato hoy
(crear repo/tenant de VER-IA y firmar un acuerdo simple) y casi imposible de
arreglar limpiamente después de tener clientes.

---

## 5. Los tres horizontes (y qué exige cada uno HOY)

### H1 — Una empresa (ahora → ~3 meses)
Se implementa: kernel con los 12 componentes (versión mínima honesta: metering a
SQLite, health a log, secretos aún locales PERO detrás del puerto de secretos),
Marketing como implementación de referencia, board = gerencia vía Teams.
**Deuda permitida:** infra local, SQLite, un solo locale — porque está detrás de
puertos del kernel y su swap no toca a los departamentos.

### H2 — Cien empresas
Cambia la implementación de los puertos, NO el estándar: PostgreSQL+RLS, Key Vault,
Container Apps, billing sobre el metering, onboarding-wizard, SLOs y on-call,
multi-locale, multi-proveedor LLM real, control plane completo con salud/costo por
tenant. Los departamentos escritos en H1 **no se tocan** — esa es la prueba de que
el kernel estaba bien diseñado.

### H3 — Marketplace (terceros construyen departamentos, agentes y conectores)

Lo que el marketplace exigirá y por eso se congela hoy (no se construye hoy):

- **§5.1 Registry + conformance:** un paquete es "VER-OS certified" si pasa la **suite
  de conformance** (tests ejecutables contra los 12 componentes: ¿respeta capacidades?
  ¿emite lifecycle events? ¿su journal es completo? ¿sobrevive kill del proceso sin
  duplicar side-effects?). La suite se escribe una vez que exista v1.0 — pero los tests
  de contrato del kernel en F0 son su semilla directa.
- **§5.2 Niveles de confianza:** `first_party` (nuestro código, in-process) ·
  `partner` (revisado, mismo runtime con capacidades acotadas) · `community`
  (sandboxed: proceso/contenedor aparte, SOLO contratos y eventos, cero acceso a
  storage del tenant, capacidades mínimas). El modelo de capacidades del invariante #2
  es lo que hace esto posible sin rediseño.
- **§5.3 Economía de terceros:** el metering por departamento (invariante #7) permite
  revenue-share por paquete sin construir nada nuevo: ya sabemos cuánto consume y
  factura cada departamento por tenant.
- **Qué NO hacemos hoy:** SDK público, portal de publishers, review process, sandbox
  runtime. Solo garantizamos que nada de lo de hoy lo impida (manifest, capacidades,
  contratos, metering — todos invariantes).

---

## 6. Gobernanza del estándar

- El spec vive versionado en el repo (`docs/ver-os/` cuando exista el repo VER-IA);
  cambios por PR con etiqueta del nivel normativo tocado. Cambiar un [INVARIANTE]
  exige decisión del board de VER-IA y plan de migración publicado.
- Registro de decisiones del estándar (ADRs cortos: contexto → decisión → porqué).
- v0.1 (hoy) → v1.0 tras un trimestre de la implementación de referencia →
  la suite de conformance acompaña a v1.0.

---

## 7. Impacto en el plan de Marketing (delta vs v3)

- **F0 crece ~2–3 días:** el kernel implementa explícitamente los 12 componentes en
  versión mínima (lo nuevo real: puerto de storage, puerto de secretos, metering,
  health estándar, estados de ciclo de vida — el resto ya estaba en v3).
- **Los 8 invariantes de §1.3 se aplican desde el primer commit** (son baratos hoy).
- Todo lo demás del plan F1–F7 queda igual. Marketing gana el título de
  "implementación de referencia VER-OS" y la responsabilidad que implica: lo que le
  duela al construirse, corrige el estándar antes de v1.0.

---

## 8. Decisiones que necesito del board (además de las 10 de Marketing v3)

1. **Separación corporativa (bloqueante):** ¿autorizan crear el repo GitHub + tenant
   Azure propios de VER-IA y formalizar el acuerdo de IP con Biodegradables antes de
   F0? Sin esto, VER-OS nace legalmente dentro del cliente #1.
2. **Ratificar los 8 invariantes de §1.3** — son las únicas decisiones de hoy
   imposibles de deshacer barato.
3. **Política de datos cross-tenant:** ¿confirman Profession Brain solo-agregado con
   opt-out por tenant como término contractual del SaaS?
4. **Ratificar la secuencia del estándar:** v0.1 ahora (invariantes) → v1.0 extraída
   de la implementación de referencia tras un trimestre → conformance suite con v1.0.
   (Mi recomendación fuerte: NO especificar más que esto hoy.)
