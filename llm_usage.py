"""llm_usage — ledger DETERMINISTA de consumo de IA por tenant (F3 VER-IA).

Registra cada llamada a un modelo (tenant, agente, modelo, tokens, costo) y
agrega por día. Es el COGS de VER-IA: cuánto cuesta operar cada cliente, por
agente y por modelo, al centavo.

Principios de diseño:
- Determinista: costo = tokens × tabla de precios versionada. Nada de IA
  midiendo IA. (Una interfaz conversacional puede consultar este ledger
  después — pero el dato lo produce aritmética, no un modelo.)
- Inofensivo: `record()` JAMÁS lanza. Si el metering falla (disco, lock,
  modelo sin precio), el producto sigue funcionando y queda un warning.
- Multi-proveedor por esquema: la clave es el model-id string — un modelo de
  otro proveedor solo necesita su entrada en PRICES.
- Agregado diario por (agente, modelo): el archivo crece por días, no por
  llamadas. Retención RETENTION_DAYS.

State: STATE_DIR/llm_usage.json vía safe_json (atómico + lock + backup).
CLI:   python llm_usage.py status [YYYY-MM]
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import safe_json

logger = logging.getLogger("llm_usage")

LOCAL_TZ = timezone(timedelta(hours=-5))  # consistente con send_ledger
USAGE_PATH = (
    Path(os.environ.get("STATE_DIR") or str(Path.home() / ".claude-agent"))
    / "llm_usage.json"
)
RETENTION_DAYS = 400  # ~13 meses: alcanza para comparativos año contra año

# ===== Tabla de precios (USD por millón de tokens) =====
# Fuente: platform.claude.com/docs/en/pricing (verificada 2026-07-03 vía la
# referencia oficial del API). Actualizar PRICE_VERSION al tocarla — el costo
# se calcula y PERSISTE al momento del registro, así que un cambio de precios
# no reescribe el histórico.
# tests/test_llm_usage.py fija que TODO modelo usado por los agentes de la
# plataforma tenga entrada aquí (cambiar de modelo sin precio rompe CI).
PRICE_VERSION = "2026-07-03"
CACHE_WRITE_MULT = 1.25   # cache write 5-min TTL = 1.25x input
CACHE_READ_MULT = 0.10    # cache read = 0.1x input
PRICES_USD_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-sonnet-5":   {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00},
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00},
    "claude-opus-4-7":   {"input": 5.00, "output": 25.00},
    "claude-opus-4-6":   {"input": 5.00, "output": 25.00},
    "claude-fable-5":    {"input": 10.00, "output": 50.00},
}


def _tenant() -> str:
    return os.environ.get("TENANT_SLUG", "biodegradables").strip() or "biodegradables"


def _today_iso() -> str:
    return datetime.now(LOCAL_TZ).date().isoformat()


def _default() -> dict[str, Any]:
    return {"tenant": _tenant(), "price_version": PRICE_VERSION, "days": {}}


def cost_usd(model: str, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
    """Costo en USD de una llamada. Modelo sin precio → tarifa del modelo
    default de la plataforma + warning (mejor un estimado visible que un $0
    silencioso)."""
    price = PRICES_USD_PER_MTOK.get(model)
    if price is None:
        logger.warning(
            "llm_usage: modelo %s SIN precio en PRICES_USD_PER_MTOK — "
            "estimando con tarifa sonnet; agregar la entrada", model,
        )
        price = PRICES_USD_PER_MTOK["claude-sonnet-4-6"]
    return (
        input_tokens * price["input"]
        + output_tokens * price["output"]
        + cache_write_tokens * price["input"] * CACHE_WRITE_MULT
        + cache_read_tokens * price["input"] * CACHE_READ_MULT
    ) / 1_000_000


def _usage_field(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        v = usage.get(name)
    else:
        v = getattr(usage, name, None)
    return int(v or 0)


def _prune(data: dict[str, Any]) -> None:
    cutoff = (datetime.now(LOCAL_TZ) - timedelta(days=RETENTION_DAYS)).date().isoformat()
    days = data.get("days", {})
    for d in [k for k in days if k < cutoff]:
        del days[d]


def record(agent: str, model: str, usage: Any) -> None:
    """Registra una llamada a un modelo. `usage` es el objeto usage de la
    respuesta del API de Anthropic (o un dict equivalente con input_tokens,
    output_tokens, cache_creation_input_tokens, cache_read_input_tokens).

    NUNCA lanza: un fallo de metering no puede tumbar al agente que mide.
    """
    try:
        inp = _usage_field(usage, "input_tokens")
        out = _usage_field(usage, "output_tokens")
        cw = _usage_field(usage, "cache_creation_input_tokens")
        cr = _usage_field(usage, "cache_read_input_tokens")
        usd = round(cost_usd(model, inp, out, cw, cr), 6)
        hoy = _today_iso()

        def mutate(data: dict[str, Any]) -> None:
            data.setdefault("tenant", _tenant())
            data["price_version"] = PRICE_VERSION
            _prune(data)
            slot = (
                data.setdefault("days", {})
                .setdefault(hoy, {})
                .setdefault(agent, {})
                .setdefault(model, {
                    "calls": 0, "input_tokens": 0, "output_tokens": 0,
                    "cache_write_tokens": 0, "cache_read_tokens": 0,
                    "cost_usd": 0.0,
                })
            )
            slot["calls"] += 1
            slot["input_tokens"] += inp
            slot["output_tokens"] += out
            slot["cache_write_tokens"] += cw
            slot["cache_read_tokens"] += cr
            slot["cost_usd"] = round(slot["cost_usd"] + usd, 6)

        safe_json.locked_update(USAGE_PATH, _default, mutate)
    except Exception:
        logger.exception("llm_usage.record falló (el agente sigue normal)")


def summary(month: str | None = None) -> dict[str, Any]:
    """Resumen de un mes ('YYYY-MM', default el actual): totales por agente,
    por modelo y por día, más el total en USD."""
    month = month or _today_iso()[:7]
    data = safe_json.load_json(USAGE_PATH, _default)
    by_agent: dict[str, float] = {}
    by_model: dict[str, float] = {}
    by_day: dict[str, float] = {}
    tokens = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
    calls = 0
    for day, agents in data.get("days", {}).items():
        if not day.startswith(month):
            continue
        for agent, models in agents.items():
            for model, s in models.items():
                usd = s.get("cost_usd", 0.0)
                by_agent[agent] = round(by_agent.get(agent, 0.0) + usd, 6)
                by_model[model] = round(by_model.get(model, 0.0) + usd, 6)
                by_day[day] = round(by_day.get(day, 0.0) + usd, 6)
                calls += s.get("calls", 0)
                tokens["input"] += s.get("input_tokens", 0)
                tokens["output"] += s.get("output_tokens", 0)
                tokens["cache_write"] += s.get("cache_write_tokens", 0)
                tokens["cache_read"] += s.get("cache_read_tokens", 0)
    return {
        "tenant": data.get("tenant", _tenant()),
        "month": month,
        "total_usd": round(sum(by_day.values()), 4),
        "calls": calls,
        "tokens": tokens,
        "by_agent": dict(sorted(by_agent.items(), key=lambda kv: -kv[1])),
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
        "by_day": dict(sorted(by_day.items())),
        "price_version": data.get("price_version", PRICE_VERSION),
    }


def month_cost(month: str | None = None) -> float:
    return summary(month)["total_usd"]


def monthly_budget_usd() -> float | None:
    """Presupuesto mensual de IA del tenant (env LLM_BUDGET_MONTHLY_USD).
    None = sin presupuesto configurado."""
    raw = os.environ.get("LLM_BUDGET_MONTHLY_USD", "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("LLM_BUDGET_MONTHLY_USD inválido: %r", raw)
        return None


def budget_status(month: str | None = None) -> dict[str, Any]:
    """{spent_usd, budget_usd, exceeded} del mes. budget_usd None = sin límite."""
    spent = month_cost(month)
    budget = monthly_budget_usd()
    return {
        "spent_usd": spent,
        "budget_usd": budget,
        "exceeded": budget is not None and spent >= budget,
    }


def _cli(argv: list[str]) -> int:
    args = [a for a in argv if a != "status"]  # `status` es el subcomando, no el mes
    month = args[0] if args else None
    s = summary(month)
    print(f"Consumo de IA — tenant {s['tenant']} — {s['month']} "
          f"(precios v{s['price_version']})")
    print(f"  TOTAL: ${s['total_usd']:.4f} USD en {s['calls']} llamadas")
    t = s["tokens"]
    print(f"  Tokens: in={t['input']:,} out={t['output']:,} "
          f"cache_write={t['cache_write']:,} cache_read={t['cache_read']:,}")
    if s["by_agent"]:
        print("  Por agente:")
        for agent, usd in s["by_agent"].items():
            print(f"    {agent:<28} ${usd:.4f}")
        print("  Por modelo:")
        for model, usd in s["by_model"].items():
            print(f"    {model:<28} ${usd:.4f}")
    b = budget_status(month)
    if b["budget_usd"] is not None:
        estado = "EXCEDIDO ⚠️" if b["exceeded"] else "OK"
        print(f"  Presupuesto: ${b['spent_usd']:.2f} / ${b['budget_usd']:.2f} [{estado}]")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
