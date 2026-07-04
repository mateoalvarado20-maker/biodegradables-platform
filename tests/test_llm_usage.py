"""Tests F3 (VER-IA 2026-07-03): metering determinista de consumo de IA.

Propiedades clave:
- El costo es aritmética exacta contra la tabla de precios versionada.
- record() JAMÁS lanza (un fallo de metering no tumba al agente).
- Todo modelo usado por los agentes de la plataforma tiene precio (gate:
  cambiar de modelo sin actualizar la tabla rompe CI).
- Presupuesto mensual por env var con estado exceeded.
"""
from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def lu(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_BUDGET_MONTHLY_USD", raising=False)
    import safe_json
    importlib.reload(safe_json)
    import llm_usage
    return importlib.reload(llm_usage)


def _usage(inp=0, out=0, cw=0, cr=0):
    return SimpleNamespace(
        input_tokens=inp, output_tokens=out,
        cache_creation_input_tokens=cw, cache_read_input_tokens=cr,
    )


# ---------- Aritmética de costos (tarifas oficiales sonnet-4-6) ----------

def test_costo_sonnet_exacto(lu):
    # 1M in ($3) + 1M out ($15) = $18
    assert lu.cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == 18.0
    # cache write 1M = $3 * 1.25 = $3.75 ; cache read 1M = $3 * 0.10 = $0.30
    assert lu.cost_usd("claude-sonnet-4-6", 0, 0, 1_000_000, 0) == 3.75
    assert lu.cost_usd("claude-sonnet-4-6", 0, 0, 0, 1_000_000) == pytest.approx(0.30)
    # llamada típica del Data Bot: 3k in, 700 out, 7k cache read
    esperado = (3000 * 3 + 700 * 15 + 7000 * 3 * 0.10) / 1_000_000
    assert lu.cost_usd("claude-sonnet-4-6", 3000, 700, 0, 7000) == pytest.approx(esperado)


def test_modelo_sin_precio_estima_y_no_lanza(lu):
    # No lanza; estima con la tarifa default (visible en logs)
    assert lu.cost_usd("modelo-inventado-9000", 1_000_000, 0) == 3.0


def test_modelos_de_los_agentes_tienen_precio(lu):
    """Gate: si un agente cambia de modelo sin agregar la tarifa, CI falla.
    (apollo_completion_notifier retirado 2026-07-04 — ya no se verifica.)"""
    import ask_agent
    import reply_agent
    import news_brief
    for model in {ask_agent.MODEL, reply_agent.MODEL, news_brief.MODEL}:
        assert model in lu.PRICES_USD_PER_MTOK, (
            f"{model} sin precio en llm_usage.PRICES_USD_PER_MTOK"
        )


# ---------- record: agregación diaria ----------

def test_record_agrega_por_dia_agente_modelo(lu):
    lu.record("data_bot", "claude-sonnet-4-6", _usage(inp=1000, out=500))
    lu.record("data_bot", "claude-sonnet-4-6", _usage(inp=2000, out=100, cr=5000))
    lu.record("reply_agent", "claude-sonnet-4-6", _usage(inp=100, out=50))

    s = lu.summary()
    assert s["calls"] == 3
    assert s["tokens"]["input"] == 3100
    assert s["tokens"]["output"] == 650
    assert s["tokens"]["cache_read"] == 5000
    assert set(s["by_agent"]) == {"data_bot", "reply_agent"}
    assert s["total_usd"] == pytest.approx(
        lu.cost_usd("claude-sonnet-4-6", 3100, 650, 0, 5000), abs=1e-4
    )
    assert s["tenant"] == "biodegradables"


def test_record_acepta_dict_y_none(lu):
    lu.record("x", "claude-sonnet-4-6", {"input_tokens": 10, "output_tokens": 5})
    lu.record("x", "claude-sonnet-4-6", None)  # usage ausente → 0s, no lanza
    s = lu.summary()
    assert s["calls"] == 2
    assert s["tokens"]["input"] == 10


def test_record_jamas_lanza(lu, monkeypatch):
    """Si el disco/lock falla, el agente que mide NO puede caerse."""
    import safe_json
    def boom(*a, **k):
        raise OSError("disco lleno")
    monkeypatch.setattr(safe_json, "locked_update", boom)
    lu.record("data_bot", "claude-sonnet-4-6", _usage(inp=100))  # no lanza


def test_summary_filtra_por_mes(lu):
    import safe_json
    lu.record("a", "claude-sonnet-4-6", _usage(inp=1000, out=0))
    # Inyectar un día de OTRO mes directamente al state
    data = safe_json.load_json(lu.USAGE_PATH, lu._default)
    data["days"]["1999-01-15"] = {
        "a": {"claude-sonnet-4-6": {
            "calls": 1, "input_tokens": 999, "output_tokens": 0,
            "cache_write_tokens": 0, "cache_read_tokens": 0, "cost_usd": 9.99,
        }}
    }
    safe_json.save_json(lu.USAGE_PATH, data)
    hoy = lu.summary()               # mes actual: no incluye 1999
    assert hoy["tokens"]["input"] == 1000
    viejo = lu.summary("1999-01")
    assert viejo["total_usd"] == 9.99


def test_prune_borra_dias_viejos(lu):
    import safe_json
    data = lu._default()
    data["days"]["2000-01-01"] = {"a": {}}
    safe_json.save_json(lu.USAGE_PATH, data)
    lu.record("a", "claude-sonnet-4-6", _usage(inp=1))  # dispara prune
    data = safe_json.load_json(lu.USAGE_PATH, lu._default)
    assert "2000-01-01" not in data["days"]


# ---------- Presupuesto ----------

def test_presupuesto_ausente(lu):
    b = lu.budget_status()
    assert b["budget_usd"] is None
    assert b["exceeded"] is False


def test_presupuesto_excedido(lu, monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", "0.10")
    # $0.15 de gasto: 10k in + 8k out sonnet = 0.03 + 0.12
    lu.record("data_bot", "claude-sonnet-4-6", _usage(inp=10_000, out=8_000))
    b = lu.budget_status()
    assert b["budget_usd"] == 0.10
    assert b["spent_usd"] >= 0.10
    assert b["exceeded"] is True


def test_presupuesto_invalido_no_lanza(lu, monkeypatch):
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", "cien")
    assert lu.monthly_budget_usd() is None


# ---------- Job de alerta del bot ----------

def test_budget_check_alerta_una_vez_por_dia(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_BUDGET_MONTHLY_USD", "0.01")
    import safe_json, send_ledger, llm_usage
    importlib.reload(safe_json)
    importlib.reload(send_ledger)
    llm_usage = importlib.reload(llm_usage)
    import teams_bot
    bot = importlib.reload(teams_bot)

    llm_usage.record("data_bot", "claude-sonnet-4-6",
                     _usage(inp=100_000, out=100_000))  # ~$1.80 > $0.01
    import graph_mail
    envios: list[dict] = []
    monkeypatch.setattr(graph_mail, "send", lambda **kw: envios.append(kw),
                        raising=False)
    asyncio.run(bot._job_llm_budget_check())
    asyncio.run(bot._job_llm_budget_check())  # mismo día → throttled
    assert len(envios) == 1
    assert "Presupuesto de IA" in envios[0]["subject"]


def test_budget_check_sin_presupuesto_no_envia(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.delenv("LLM_BUDGET_MONTHLY_USD", raising=False)
    import safe_json, send_ledger, llm_usage
    importlib.reload(safe_json)
    importlib.reload(send_ledger)
    importlib.reload(llm_usage)
    import teams_bot
    bot = importlib.reload(teams_bot)
    import graph_mail
    envios: list = []
    monkeypatch.setattr(graph_mail, "send", lambda **kw: envios.append(kw),
                        raising=False)
    asyncio.run(bot._job_llm_budget_check())
    assert envios == []
