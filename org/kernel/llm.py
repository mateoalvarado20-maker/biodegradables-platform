"""Puente LLM del kernel VER-OS (ROADMAP.md F0.10).

Una llamada a un modelo hecha por un departamento queda registrada en DOS
ledgers a la vez:

- `llm_usage.py` (plataforma): COGS por tenant/agente/modelo — la fuente del
  billing. El agente se registra como "<dept_id>:<agent>" para que el ledger
  de plataforma también atribuya por departamento.
- `Meter` del departamento (kernel): la fuente del presupuesto del charter.

Filosofía heredada de llm_usage: REGISTRAR jamás lanza — un fallo de metering
no tumba al agente que mide. El enforcement duro no vive aquí: vive en
`Department.ensure_budget()`, que se llama ANTES de gastar.
"""

from __future__ import annotations

import logging
from typing import Any

import llm_usage
from org.kernel.department import Department

logger = logging.getLogger("org.kernel.llm")


def _tok(usage: Any, name: str) -> int:
    if usage is None:
        return 0
    if isinstance(usage, dict):
        value = usage.get(name)
    else:
        value = getattr(usage, name, None)
    return int(value or 0)


def record_llm_call(dept: Department, agent: str, model: str, usage: Any) -> float:
    """Registra la llamada en ambos ledgers. Devuelve el costo estimado en USD
    (0.0 si el registro falló). Nunca lanza."""
    usd = 0.0
    try:
        inp = _tok(usage, "input_tokens")
        out = _tok(usage, "output_tokens")
        cache_w = _tok(usage, "cache_creation_input_tokens")
        cache_r = _tok(usage, "cache_read_input_tokens")
        usd = round(llm_usage.cost_usd(model, inp, out, cache_w, cache_r), 6)

        llm_usage.record(f"{dept.dept_id}:{agent}", model, usage)
        dept.meter.record(
            "llm_tokens",
            qty=inp + out,
            usd=usd,
            meta={"agent": agent, "model": model},
        )
    except Exception:
        usd = 0.0
        logger.exception("record_llm_call falló (el agente sigue normal)")
    return usd
