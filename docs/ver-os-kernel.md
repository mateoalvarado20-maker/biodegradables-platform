# Kernel VER-OS (`org/`) — guía mínima

**Estándar:** `PROPUESTA_VER_OS.md` v0.1 · **Backlog:** `ROADMAP.md` F0 ·
**Tests:** `tests/test_veros_kernel.py` · **Demo:** `python -m org.demo`

El kernel es el chasis genérico de departamento de la empresa dirigida por IA.
Un departamento de dominio (Marketing, Comercial…) se construye componiendo un
`Department` con sus agentes propios; jamás toca SQLite directo.

## Uso

```python
from org.kernel import Charter, Department, TenantStore, load_manifest

store = TenantStore("biodegradables")          # 1 archivo SQLite por tenant
manifest = load_manifest("org/packages/marketing/verops.yaml")
charter = Charter(okrs=("0→3k seguidores Q3",), budget_usd_month=60.0,
                  approved_by="dsanchez@...", approved_at="2026-07-06")
dept = Department(manifest, charter, store, granted_capabilities={"llm", "notify"})

dept.lifecycle_to("installed"); dept.lifecycle_to("onboarding"); dept.lifecycle_to("active")
dept.ensure_capability("llm")                  # CapabilityError si no fue otorgada
dept.ensure_budget(0.05)                       # BudgetExceeded = corte duro
dept.decide("elegí X", context_refs=["playbook:r12"], correlation_id=cid)
dept.emit("content.published", {"post_id": "p1"}, correlation_id=cid)
dept.meter.record("llm_tokens", qty=1200, usd=0.02)
dept.health()                                  # dict estándar para el control plane
```

## Qué es invariante vs convención (VER-OS §1.2)

**Invariantes ya enforced por el kernel:**
- 1 archivo de base por tenant → aislamiento por construcción (`store.py`).
- `org_events`, `decision_journal` y `metering` son append-only por triggers SQL.
- Capacidades: solo se otorga lo declarado en el manifest; solo se usa lo otorgado.
- Presupuesto del charter corta (excepción), no advierte.
- Autonomía se gana de a un nivel, con evidencia, bajo el `max_level` del manifest;
  todo cambio queda en journal + evento.
- Charter exige OKRs y aprobador humano.

**Convenciones (pueden cambiar en v1.0 sin migración cara):** el formato fino del
manifest, el validador de contratos (Python puro, no JSON Schema completo), los
nombres de las tablas, `Department.health()` como dict.

## Contratos (`org/contracts/`)

`Nombre@N.json` con `fields` (type/required/enum). Validación:

```python
from org.contracts import validate_payload
validate_payload("LeadOutcome@1", {"lead_id": "l1", "outcome": "won", "closed_at": "..."})
```

Registrados: `LeadHandoff@1`, `LeadOutcome@1`, `WeeklyDeptReport@1`,
`EscalationRequest@1`. Breaking change = archivo nuevo `@N+1`, ambos conviven.

## Puente LLM (F0.10)

Toda llamada a un modelo hecha por un departamento se registra en ambos ledgers:

```python
from org.kernel.llm import record_llm_call
resp = client.messages.create(...)
record_llm_call(dept, agent="guionista", model=MODEL, usage=resp.usage)
```

Queda en `llm_usage` (COGS de plataforma, agente `"<dept_id>:<agent>"`) y en el
`Meter` del departamento (presupuesto del charter). Registrar jamás lanza; el
corte duro es `dept.ensure_budget()` ANTES de gastar.

## Pendiente en F0 (ver ROADMAP)

- F0.12: revisión técnica de fase (¿ajustes a VER-OS v0.1?).

## Decisiones de implementación que la revisión de fase debe validar

1. SQLite por tenant con triggers como enforcement (vs tabla compartida + RLS
   futura): elegido por aislamiento por construcción en H1.
2. Validador de contratos casero (sin dependencia `jsonschema`): 0 deps nuevas;
   revisar si v1.0 amerita JSON Schema real.
3. `processed_events` como idempotencia de consumidor (claim-once) en vez de
   offsets: más simple y suficiente para volúmenes H1.
