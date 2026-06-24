# Propuesta técnica — Entorno DEMO comercial

**Fecha:** 2026-06-24
**Autor:** análisis Claude sobre la solución actual (Biodegradables Ecuador)
**Estado:** PROPUESTA — no se ha tocado código. Requiere luz verde antes de construir.
**Objetivo:** convertir la plataforma actual en un DEMO reutilizable, con empresa
ficticia y datos sintéticos, para vender el servicio a múltiples prospectos sin
exponer datos reales de ningún cliente.

---

## 0. TL;DR (lo que recomiendo)

> **El DEMO NO es un proyecto aparte: es el "tenant #2" que la arquitectura
> multiempresa ya estaba esperando.** Construirlo avanza el roadmap F7 ("onboarding
> del segundo cliente") *y* produce el activo de ventas al mismo tiempo.

Las 3 opciones que planteaste **no son alternativas excluyentes — son 3 capas** del
mismo producto:

| Tu opción | Qué es en realidad | Veredicto |
|---|---|---|
| **Opción 1** — Empresa Demo Completa | La capa de **DATOS** (tenant ficticio + datos sintéticos) | **Fundación obligatoria** |
| **Opción 2** — Bot Demo en vivo | La capa de **CANALES** (Teams + correos reales en una bandeja demo) | Alto impacto, fase 2 |
| **Opción 3** — Caso de uso guiado | La capa de **NARRATIVA** (guion de presentación) | Máximo impacto / esfuerzo, hacer ya |

**Recomendación:** construir las tres como capas en fases. El MVP demostrable
(Opción 1 + Opción 3 + sandbox de correo) se logra en **~6–10 días** de trabajo
asistido; la versión "SaaS listo para vender" con bots en vivo en **~12–18 días**.

Las **3 piezas críticas** a construir (no existen hoy):
1. **Generador de datos sintéticos coherentes** (`demo_seed.py`) — el corazón del demo.
2. **Sandbox de correo / anti-fuga** (`DEMO_MODE`) — red de seguridad para que jamás
   salga un dato real ni un correo a una dirección real.
3. **De-hardcodear la identidad** (nombres/correos/prompts incrustados en
   `ask_agent.py`, `teams_bot.py`, `daily_report.py`, `news_brief.py`).

---

## 1. Análisis profundo de la solución actual

### 1.1 Inventario de componentes demostrables

| Componente | Archivo(s) | ¿Demostrable? | Valor comercial |
|---|---|---|---|
| Reporte comercial 8 AM | `daily_report.py` | ✅ Sí | ⭐⭐⭐ Alto (KPIs, meta, semáforo) |
| Reporte de logística | `daily_logistics_report.py` | ✅ Sí | ⭐⭐⭐ Muy visual (envíos por ciudad/provincia) |
| Reporte de cartera | dentro de `daily_report.py` + `contifico_client.cartera_*` | ✅ Sí | ⭐⭐⭐ Alto (riesgo financiero) |
| Recap mensual (ventas + actividades) | `monthly_recap.py` | ✅ Sí | ⭐⭐ Medio |
| Resumen diario del equipo | job `consolidated_daily_summary` (`teams_bot.py`) | ✅ Sí | ⭐⭐⭐ Alto (gerencia recibe 1 correo) |
| Data Bot (chat de datos) | `teams_bot.py` + `ask_agent.py` modo `data` | ✅ Sí | ⭐⭐⭐ "Wow" en vivo |
| Activities Bot (check-in) | `teams_bot.py` + Adaptive Cards | ✅ Sí | ⭐⭐⭐ "Wow" (formularios) |
| Formularios a colaboradores | Adaptive Card de check-in + cierre de caja | ✅ Sí | ⭐⭐⭐ Muy tangible |
| Cobranzas auto-asignadas | job `auto_assign_cobranzas` | ✅ Sí | ⭐⭐ Medio |
| Recordatorios recurrentes | `reminders.py` | ✅ Sí | ⭐ Bajo |
| Brief de noticias diario | `news_brief.py` | ✅ Sí (con web_search real) | ⭐⭐ Medio (IA "viva") |
| Forecasting de ventas | `forecasting.py` | ✅ Sí | ⭐⭐ Medio |
| Reply agent (correos a prospectos) | `reply_agent.py` + Apollo | ⚠️ Parcial | ⭐⭐ Medio (necesita bandeja demo) |
| Dashboard Power BI | externo (app.powerbi.com) | ❌ Difícil | — (mostrar screenshot/mock) |
| Integración WordPress | `wp_*.py` | ⚠️ Solo lectura | ⭐ Bajo |

**Disparadores ya existentes que sirven como "control remoto" del demo** — esto es
oro: `teams_bot.py` ya expone endpoints admin que disparan cualquier reporte/flujo
bajo demanda (requieren header `X-Admin-Token`):
`/admin/trigger-checkin`, `/admin/trigger-reminders`, `/admin/trigger-cobranzas`,
`/admin/trigger-weekly-summaries`, `/admin/trigger-consolidated-daily-summary`,
`/admin/seed-template-for-user`, etc. → **en una demo, presionas un botón y el
correo "de las 8 AM" aparece en vivo frente al prospecto.**

### 1.2 Dónde entran los datos reales (los "grifos" a cerrar)

Todos los clientes leen credenciales de env vars y tienen URL base fija → **punto de
inyección limpio**. Formas y archivos exactos:

| Cliente | Credencial (env var) | Funciones que devuelven datos | Dificultad de sustituir |
|---|---|---|---|
| `contifico_client.py` | `CONTIFICO_API_TOKEN` | `get_documentos`, `ventas_dia/_rango`, `ventas_por_ciudad`, `top_vendedores/_clientes`, `cumplimiento_mes`, `cartera_*`, `envios_dia_gye` | **Trivial** |
| `hubspot_client.py` | `HUBSPOT_TOKEN` | `leads_ayer/_30d`, `deals_ganados_ayer`, `pipeline_abierto`, `deals_stuck`, `conversion_rate_30d` | **Trivial** |
| `apollo_rest.py` | `APOLLO_API_KEY` | `enrich_by_email`, `enrich_organization`, `list_sequences` | **Trivial** |
| `graph_mail.py` | `MICROSOFT_APP_*` | `send`, `send_email`, `lookup_user_email` | **Trivial** (redirigir TO) |
| `outlook_client.py` | MSAL (compartido `pbi_cloud`) | `list_unread_inbox`, `get_message`, `create_draft_reply` | Moderada (auth compartida) |
| `pbi_cloud.py` | MSAL + `GRAPH_*` | `execute_dax`, `send_email` | **Ya en retirada** — el reporte migró a Contifico |

**Buena noticia:** `core/connectors/base.py` **ya define las interfaces**
`ErpConnector`, `CrmConnector`, `MailConnector` (Protocol) — pero **sin
implementaciones todavía**, y los reportes aún importan `contifico_client`
directamente. Eso define dos caminos para inyectar datos sintéticos (ver §4.2).

### 1.3 Superficie de datos sensibles (lo que DEBE anonimizarse)

Dos categorías muy distintas:

**(A) Datos que renderean los reportes en runtime** — vienen de las APIs, NO están
hardcodeados. Se anonimizan **cortando el grifo** (datos sintéticos). Incluye:
nombres de clientes, montos de facturas, direcciones, vendedores, leads, deals.

**(B) Identidad hardcodeada en el código** — el verdadero riesgo de fuga. Estos
strings están incrustados y se filtrarían aunque cambies la fuente de datos:

| Archivo | Qué está hardcodeado | Riesgo |
|---|---|---|
| `core_config.py` | correos JEFE/MIO/GABRIELA/CHECKIN | ✅ **Bajo** — ya es env-overridable + existe `tenants/biodegradables/config.yaml` |
| `ask_agent.py` | `EMAIL_TO_NAME` (Daniel Sánchez, Gabriela Sánchez, Mateo Alvarado, Gabriela Bravo, Gladys López, José Solórzano), `SUPERVISORS_ONLY_EMAILS`, `ASISTENTE_EMAILS`, `JOSE_EMAIL_CONS`, system prompts con "Biodegradables Ecuador" / "Quito y Guayaquil" | 🔴 **Crítico** (~33 ocurrencias + nombres reales en prompts) |
| `teams_bot.py` | `DATA_ALLOWED_USERS`, `FALLBACK_EMAIL`, nombres de bots | 🔴 **Alto** (~30 ocurrencias) |
| `daily_report.py` | system prompt: *"Daniel Sánchez, gerente general de Biodegradables Ecuador"*, URL del dashboard PBI real | 🟠 **Medio** |
| `news_brief.py` | prompt: *"empresa distribuidora de empaques biodegradables en Ecuador… Quito y Guayaquil"* | 🟠 **Medio** |
| `monthly_recap.py` | correos default (env-overridable) | 🟢 Bajo |
| `tests/` | `test_identity.py`, `test_delegation.py` con correos reales como fixtures | 🟢 Bajo (no se despliega) |

> **Conclusión clave:** el flag `TENANT_CONFIG_SOURCE=yaml` resuelve (A) y la parte
> de config de (B), pero **NO** los nombres incrustados en `ask_agent.py` /
> `daily_report.py` / `news_brief.py`. Esos hay que moverlos a config de tenant
> (o, como mínimo para el demo, overridearlos por env var + un `EMAIL_TO_NAME` demo).
> Esto es trabajo de refactor que **también beneficia al producto multiempresa real.**

---

## 2. Evaluación de las 3 opciones planteadas

### Opción 1 — Empresa Demo Completa (la capa de DATOS)
Crear una empresa ficticia y que todos los agentes operen sobre ella.

- ✅ **Ventajas:** es la base de todo; reutiliza el 100% del motor; coherencia total;
  reutilizable para N prospectos sin tocar nada; alinea con la arquitectura
  multiempresa ya iniciada (se vuelve `tenants/demo/`).
- ❌ **Desventajas:** requiere construir el generador de datos sintéticos coherentes
  (la pieza más cara); requiere de-hardcodear la identidad.
- 🛠️ **Esfuerzo:** **Medio-Alto (4–7 días).** Es la inversión principal.
- **Veredicto:** **OBLIGATORIA.** Sin esto no hay demo seguro.

### Opción 2 — Bot Demo en vivo (la capa de CANALES)
Mostrar formularios, respuestas, correos, alertas y agentes trabajando en tiempo real.

- ✅ **Ventajas:** máximo factor "wow"; el prospecto ve el producto en su Teams/correo
  real; tangible y memorable.
- ❌ **Desventajas:** requiere recursos Azure demo separados (Bot registration, App
  Service o contenedor, bandeja de correo demo); más superficie de fuga si no está
  el sandbox; mantenimiento.
- 🛠️ **Esfuerzo:** **Alto (4–6 días)** además de la Opción 1.
- **Veredicto:** **Fase 2.** Altísimo impacto, pero solo cuando la base sea sólida.

### Opción 3 — Caso de uso guiado (la capa de NARRATIVA)
Recorrido: captura → colaboradores → agentes → reportes → gerencia.

- ✅ **Ventajas:** lo más barato; estructura la venta; funciona incluso con
  screen-share + correos pre-generados; reduce el riesgo de improvisar frente al
  prospecto.
- ❌ **Desventajas:** por sí solo no impresiona (necesita datos de Opción 1 detrás).
- 🛠️ **Esfuerzo:** **Bajo (0.5–1 día)** una vez que existe el dataset demo.
- **Veredicto:** **Hacer ya.** Es el guion que envuelve todo.

### Mejor alternativa (síntesis recomendada)
**"Demo = Tenant #2 + Conector Sintético + Sandbox de correo + Estado pre-sembrado +
Panel de control de presentación."** Combina las tres opciones por capas y reutiliza
la maquinaria multiempresa existente. Detalle en §4.

---

## 3. La empresa ficticia (propuesta completa)

Diseñada para ser **paralela** a Biodegradables (mismo "molde" de negocio → reusa toda
la lógica: 2 ciudades, sucursales, vendedores, cartera, logística) pero **sin ninguna
colisión** con datos reales. Si no te gusta el nombre, cambiarlo = 1 campo en el YAML.

### 3.1 Identidad corporativa

| Campo | Valor propuesto |
|---|---|
| **Razón social** | Distribuidora Andina del Pacífico S.A. |
| **Nombre comercial / marca** | **Andex** |
| **Slug (tenant)** | `andex` |
| **Sector** | Distribución mayorista de productos de consumo masivo (descartables, limpieza, cuidado personal) |
| **Eslogan** | "Andex — Distribución que mueve tu negocio" |
| **Dominio de correo** | `@andexdemo.com` (ficticio, no registrado / o registrarlo barato) |
| **Color de marca** | `#0B6E99` (azul corporativo — distinto del verde real) |
| **Ciudades operativas** | Quito (UIO) y Guayaquil (GYE) *(geografía pública, no es PII; conservarla hace el demo más realista para prospectos ecuatorianos)* |
| **Web (mock)** | `andexdemo.com` |

### 3.2 Equipo de trabajo (personas ficticias)

| Rol | Nombre ficticio | Correo demo |
|---|---|---|
| Gerente general (recibe reportes) | **Roberto Salinas** | `rsalinas@andexdemo.com` |
| Gerente comercial | **Carolina Vega** | `cvega@andexdemo.com` |
| Analista comercial (admin/operador) | **Andrés Mora** | `amora@andexdemo.com` |
| Asistente sucursal Guayaquil | **Pamela Ortiz** | `info@andexdemo.com` |
| Asistente sucursal Quito | **Lucía Ramírez** | `quito@andexdemo.com` |
| Asistente 2 / despacho GYE | **Marco Tipán** | `mtipan@andexdemo.com` |

### 3.3 Equipo comercial (vendedores con cartera asignada)

| Vendedor | Ciudad | # clientes | Cuota mensual |
|---|---|---|---|
| Diana Cevallos | UIO | 22 | $32.000 |
| Jorge Andrade | UIO | 18 | $28.000 |
| Verónica Suárez | GYE | 25 | $38.000 |
| Luis Maldonado | GYE | 20 | $30.000 |

### 3.4 Catálogo de productos (ficticio, coherente con el sector)

Categorías: **Descartables** (vasos, platos, cubiertos, contenedores), **Limpieza**
(desinfectante, jabón líquido, fundas), **Cuidado personal** (toallas, papel
higiénico institucional), **Empaque** (film, cinta, cajas). ~40 SKUs con precios
plausibles ($0,80–$45). Códigos `AND-DESC-001`, etc.

### 3.5 Cartera de clientes (~90 cuentas ficticias)

Tipos: minimarkets, restaurantes, hoteles, distribuidores, instituciones. Nombres
genéricos plausibles: "Minimarket El Ahorro", "Restaurante La Sazón", "Hotel
Costa Azul", "Comercial Su Despensa", etc. Cada uno con: ciudad, vendedor asignado,
condición de crédito (contado / 15 / 30 / 45 días), dirección ficticia.

### 3.6 Metas e indicadores (cuadrados entre sí)
- Meta mensual = ventas mismo mes año anterior × **1.20** (igual factor que el real).
- Histórico ficticio de 18 meses para que el forecasting y los comparativos YoY
  tengan de dónde calcular.
- Cartera vencida objetivo ~8–12% (para que el semáforo muestre amarillo/rojo
  ocasional y se vea el valor de la alerta).

---

## 4. Arquitectura del DEMO (la propuesta técnica)

```
                    ┌─────────────────────────────────────────┐
                    │  MISMO MOTOR (sin fork, sin duplicar)     │
                    │  daily_report · logistics · monthly ·     │
                    │  teams_bot · ask_agent · forecasting ...  │
                    └───────────────┬───────────────────────────┘
            TENANT_SLUG=andex        │      DEMO_MODE=1 (interruptor maestro)
            TENANT_CONFIG_SOURCE=yaml│
                    ┌────────────────┴───────────────┐
                    ▼                                  ▼
        ┌───────────────────────┐        ┌───────────────────────────┐
        │ tenants/andex/         │        │  CAPA DE DATOS (demo)       │
        │  config.yaml           │        │  demo_seed.py  → genera     │
        │  prompts/*.md          │        │   dataset coherente sembrado│
        │  (identidad ficticia)  │        │  DemoErpConnector / shim →  │
        └───────────────────────┘        │   mismas formas que Contifico│
                                          │  DemoCrmConnector → HubSpot  │
                                          └───────────────────────────┘
                    ▼
        ┌───────────────────────────────────────────────┐
        │  SANDBOX DE CORREO (anti-fuga)                  │
        │  graph_mail: DEMO_MODE redirige TODO a          │
        │  demo@andexdemo.com, prepende [DEMO], y         │
        │  BLOQUEA cualquier destino fuera de allowlist   │
        └───────────────────────────────────────────────┘
```

### 4.1 Qué se REUTILIZA vs qué se CREA

**Reutilizar tal cual (0 cambios de lógica):**
- Todo el motor de reportes, scheduler, Adaptive Cards, plantillas HTML.
- La maquinaria multiempresa `core/` (loader, schema, prompt engine, mail renderer).
- El switch `TENANT_CONFIG_SOURCE=yaml` (ya probado con tests golden).
- Los endpoints `/admin/trigger-*` (control remoto del demo).
- `safe_json`, `send_ledger`, helpers de TZ.

**Crear nuevo (específico de demo):**
| Pieza | Archivo nuevo | Propósito |
|---|---|---|
| Config del tenant ficticio | `tenants/andex/config.yaml` + `prompts/*.md` | Identidad Andex |
| Generador de datos | `demo_seed.py` | Dataset coherente determinista |
| Conector sintético | `demo_connectors.py` (o shim de import) | Devolver datos con la forma exacta de Contifico/HubSpot/Apollo |
| Estado pre-sembrado | `seeds/andex/*.json` | `activity_state`, `dispatch_state`, `reminders`, `cierres_caja` ya poblados |
| Sandbox de correo | guard en `graph_mail.py` (gated por `DEMO_MODE`) | Anti-fuga |
| De-hardcode de identidad | edits en `ask_agent.py`, `daily_report.py`, `news_brief.py` | Que los nombres/empresa salgan del tenant, no incrustados |
| Panel de presentación | `demo_console.html` o doc de guion | Botones que disparan los `/admin/trigger-*` |

### 4.2 Cómo inyectar los datos sintéticos (2 caminos)

**Camino A — Shim de import gated por `DEMO_MODE` (pragmático, rápido).**
Como los reportes hoy importan `contifico_client` directo, se crea
`demo_connectors.py` que reimplementa las mismas funciones con las mismas formas de
retorno, y en cada módulo se hace:
```python
if os.getenv("DEMO_MODE") == "1":
    import demo_connectors as contifico_client
else:
    import contifico_client
```
- ✅ Rápido, no depende de terminar F2. ❌ Toca varios imports; "deuda" hasta F2.

**Camino B — Implementar los Protocols de `core/connectors/base.py` (limpio, futuro).**
Escribir `DemoErpConnector(ErpConnector)` / `DemoCrmConnector(CrmConnector)` y
resolver el conector por tenant. ✅ Es la arquitectura "correcta" y reusable para
clientes reales. ❌ Requiere primero **cablear** los reportes para que usen el
conector inyectado (parte de F2, aún no hecho).

> **Recomendación:** **Camino A ahora** (demo en semanas, no meses), diseñando
> `demo_connectors.py` con las mismas firmas que tendrá el `DemoErpConnector`, para
> migrar a Camino B cuando F2 aterrice. Lo mejor de ambos.

### 4.3 Generación automática de datos ficticios (`demo_seed.py`)

El corazón del demo. Principios:
- **Determinista** (seed fija) → el demo se ve igual cada vez; reproducible.
- **Coherente por construcción** (las reglas que pediste):
  1. Se generan **clientes** → cada uno con vendedor y ciudad.
  2. Se generan **facturas** por cliente (respetando estacionalidad y la meta).
  3. La **cartera** se deriva de las facturas con `saldo > 0` y su condición de crédito
     → *las facturas generan cartera automáticamente.*
  4. Los **KPIs** (ventas día/MTD, cumplimiento, top vendedores, ticket promedio) se
     **calculan desde las mismas facturas** → *los indicadores cuadran con las ventas
     por construcción, no por coincidencia.*
  5. La **logística** sale de las mismas facturas (prefijo `001-001`=GYE,
     `001-002`=UIO; dirección parseada por provincia).
  6. **Leads/deals (HubSpot)** y **prospectos (Apollo)** se generan con volúmenes
     proporcionales al pipeline.
- **"Reloj" relativo a hoy:** los datos se generan respecto a `today()` del demo, así
  el reporte de "ayer" / "MTD" siempre tiene sentido sin re-sembrar.
- **Realismo:** distribución de montos log-normal, picos de fin de mes, ~3–5 facturas
  anuladas, 1–2 clientes morosos para que el semáforo se ponga rojo.

### 4.4 Estrategia anti-fuga (crítica)

Defensa en profundidad — **ningún dato real puede aparecer accidentalmente**:

1. **Sandbox de correo (capa 1):** con `DEMO_MODE=1`, `graph_mail.send_*`
   redirige TODO destino a la allowlist demo, prepende `[DEMO]` al asunto, y
   **lanza excepción si algún destino no termina en `@andexdemo.com`** (o el dominio
   demo configurado). Imposible mandar a un correo real.
2. **Tenant aislado (capa 2):** `TENANT_SLUG=andex` → toda la config de negocio
   (destinatarios, marca, prompts) sale del YAML ficticio.
3. **Fuente de datos aislada (capa 3):** `DEMO_MODE` desconecta Contifico/HubSpot/
   Apollo reales; si faltara el shim, el cliente real exige token y **falla cerrado**
   (no devuelve datos).
4. **Credenciales separadas (capa 4):** el demo corre con su propia App
   Registration / bandeja, **sin** los tokens de producción cargados.
5. **Guardia de arranque (capa 5):** al iniciar en `DEMO_MODE`, loguear el tenant,
   el dominio de correo permitido y abortar si detecta `biodegradables` en cualquier
   destinatario resuelto. Un test (`test_demo_no_real_data.py`) que grepea el output
   renderizado buscando `biodegradables`, nombres reales, `+593` reales, etc.

---

## 5. Plan de implementación paso a paso

> Estimaciones en días de trabajo asistido (Claude + Mateo). Cada fase es entregable
> y demostrable por sí sola.

### Fase 0 — Sandbox de correo + guardia anti-fuga · **0.5–1 día**
- `DEMO_MODE` en `graph_mail.py`: redirección + allowlist + `[DEMO]` + fail-closed.
- Guardia de arranque + test anti-fuga.
- **Entregable:** seguridad garantizada antes de generar nada.

### Fase 1 — Tenant ficticio + de-hardcode de identidad · **1.5–2.5 días**
- `tenants/andex/config.yaml` + `prompts/company_context.md` + `data_bot.md`.
- Mover `EMAIL_TO_NAME`, allowlists y los strings de empresa de `ask_agent.py` /
  `daily_report.py` / `news_brief.py` a config de tenant (o env override demo).
- Activar `TENANT_CONFIG_SOURCE=yaml` + `TENANT_SLUG=andex`.
- **Entregable:** los bots se presentan como "Andex", sin un solo nombre real.

### Fase 2 — Generador de datos sintéticos · **3–5 días** (núcleo)
- `demo_seed.py` (clientes→facturas→cartera→KPIs→logística→leads).
- `demo_connectors.py` con las firmas exactas de `contifico_client` / `hubspot_client`
  / `apollo_rest`.
- Shim de import gated por `DEMO_MODE`.
- **Entregable:** correr `python daily_report.py dry` y `daily_logistics_report.py dry`
  con datos Andex 100% sintéticos y coherentes.

### Fase 3 — Estado pre-sembrado del equipo · **1–2 días**
- `seeds/andex/`: `activity_state.json` (actividades de la semana de cada
  colaborador), `dispatch_state.json` (algunos despachos OK/NO/PARCIAL),
  `reminders.json`, `cierres_caja`.
- Script `seed_demo_state.py` que los carga (idempotente).
- **Entregable:** check-in cards, resumen consolidado y cierre de caja se ven poblados.

### Fase 4 — Panel de presentación + guion · **1–2 días**
- `demo_console.html` (o colección Postman / botones) que dispara los
  `/admin/trigger-*` → reportes en vivo.
- Documentar los guiones de 10 y 30 min (§7).
- **Entregable:** demo presentable end-to-end vía screen-share + correo.

### Fase 5 (opcional) — Bots en vivo en Teams demo · **2–4 días**
- App Registration + Azure Bot + App Service/contenedor demo separados, tenant demo
  de M365 (o sandbox), 2 bots sideloaded.
- **Entregable:** el prospecto chatea con el Data Bot y llena el check-in en su Teams.

**Total MVP impactante (F0–F4): ~6–10 días. SaaS-grade con bots en vivo (F0–F5): ~12–18 días.**

---

## 6. Recomendaciones "producto SaaS listo para vender"

1. **Vende la arquitectura multiempresa, no "un script".** El demo *es* la prueba de
   que onboardeas un cliente nuevo con un `config.yaml`. Eso es el pitch SaaS.
2. **Marca configurable visible:** logo + color por tenant en cada correo (el
   `mail/renderer.py` ya inyecta branding). Que el prospecto vea SU logo en el correo
   demo = cierre emocional. (Opción "demo personalizado por prospecto" en 5 min.)
3. **Página de aterrizaje del demo** con los correos de ejemplo embebidos y un
   "tour" clicable (puede ser HTML estático que renderiza los mismos templates).
4. **Catálogo de módulos activables** (reporte comercial / logística / cartera /
   bots / cobranzas) → tabla de "planes" → conversación de precios natural.
5. **Métricas de valor:** en el guion, traducir features a ROI ("la gerencia recibe 1
   correo a las 6:30 en vez de perseguir a 5 personas").
6. **Aislamiento por cliente como argumento de seguridad:** "cada cliente, su propio
   contenedor, su Key Vault, sus datos" — diferenciador vs. una hoja de cálculo.
7. **Modo "sandbox personalizable en vivo":** poder cambiar el nombre de la empresa
   ficticia al del prospecto durante la reunión (1 campo) genera muchísimo impacto.
8. **No improvisar datos:** dataset determinista + guion → cero sorpresas en vivo.

---

## 7. Guiones de presentación

### 7.1 Demo express — 10 minutos
| Min | Bloque | Qué mostrar |
|---|---|---|
| 0–1 | Encuadre | "Andex, distribuidora con 2 sucursales. Mira cómo la gerencia se entera de todo sin perseguir a nadie." |
| 1–3 | **Correo comercial 8 AM** | Disparar `/admin/trigger-...` → llega el correo con KPIs, meta, semáforo, top vendedores. |
| 3–5 | **Reporte de logística** | El correo de envíos por ciudad/provincia + estado de despacho. |
| 5–7 | **Data Bot en vivo** | Preguntar en chat: "¿cuánto vendimos ayer?", "¿quién es el top deudor de Guayaquil?" → responde al instante. |
| 7–9 | **Check-in / formulario** | Mostrar el Adaptive Card que recibe un colaborador + el resumen consolidado que recibe gerencia. |
| 9–10 | Cierre | "Todo esto se configura por cliente en minutos. ¿Lo armamos con TUS datos?" |

### 7.2 Demo completo — 30 minutos
1. **(0–3) Contexto y dolor.** El problema: gerencia a ciegas, datos dispersos en Contifico/correo/Excel.
2. **(3–8) Captura y equipo.** Check-in diario (Adaptive Cards), cierre de caja, cobranzas auto-asignadas. *Cómo entra la información.*
3. **(8–14) Agentes procesando.** Data Bot (preguntas libres de ventas/cartera), forecasting, brief de noticias con IA. *Cómo se procesan los datos.*
4. **(14–22) Reportes a gerencia.** Comercial 8 AM, logística, cartera, recap mensual, resumen consolidado 6:30 PM. *Cómo llega a la gerencia.* (Disparar en vivo con el panel.)
5. **(22–27) Automatización y confiabilidad.** Horarios, anti-duplicado (`send_ledger`), reintentos, multiempresa (mostrar el `config.yaml` y "así onboardeamos a un cliente").
6. **(27–30) Cierre comercial.** Catálogo de módulos, aislamiento por cliente, próximos pasos y precios.

---

## 8. Decisiones (CONFIRMADAS 2026-06-24)

| # | Decisión | ✅ Confirmado |
|---|---|---|
| 1 | Nombre/sector de la empresa ficticia | **Andex** — distribuidora de consumo masivo |
| 2 | Alcance | **SaaS completo con bots en vivo (F0–F5)**, ~12–18 días |
| 3 | De-hardcode de identidad | **Refactor permanente** a config de tenant (sirve también al producto multiempresa real) |
| 4 | Bandeja de correo demo | Pendiente: M365 demo real (recomendado) vs captura local — confirmar al llegar a F5 |
| 5 | Hosting del demo | Local para F0–F4; Azure/M365 demo separado para F5 (bots en vivo) |

> Como el de-hardcode es **refactor permanente**, las Fases 1 se ejecutan tocando el
> código de producción (no parches demo): `EMAIL_TO_NAME`, allowlists y los strings de
> empresa salen a `tenants/<slug>/` y se resuelven por `TenantContext`. Esto debe ir
> respaldado por los tests de identidad existentes (`test_identity.py`,
> `test_delegation.py`) para no romper el comportamiento de Biodegradables.

---

## 9. Próximo paso sugerido
Si apruebas esta dirección, el orden de arranque es **Fase 0 → Fase 1 → Fase 2**.
La Fase 0 (sandbox anti-fuga) se hace primero **siempre**, antes de generar ningún
dato, para que sea imposible que un correo salga a un destino real durante el desarrollo.
