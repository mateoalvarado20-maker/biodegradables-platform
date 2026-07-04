"""El reporte comercial diario NO sale el día 1 del mes (2026-07-04): ese día
gerencia recibe el recap mensual de ventas a la misma hora. El guard aplica a
las 3 superficies (job, catch-up, dead-man) vía _morning_sales_skip_hoy.
"""
from __future__ import annotations

import importlib
from datetime import datetime

import pytest


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    importlib.reload(send_ledger)
    import teams_bot
    return importlib.reload(teams_bot)


def test_skip_solo_el_dia_1(bot):
    import core_config
    assert "monthly_sales_recap" in core_config.JOB_SCHEDULES
    assert bot._morning_sales_skip_hoy(datetime(2026, 8, 1, 8, 0)) is True
    assert bot._morning_sales_skip_hoy(datetime(2026, 8, 2, 8, 0)) is False
    assert bot._morning_sales_skip_hoy(datetime(2026, 7, 15, 8, 0)) is False


def test_sin_recap_mensual_no_se_salta(bot, monkeypatch):
    """Tenant sin monthly_sales_recap contratado → el comercial sale normal
    incluso el día 1."""
    import core_config
    sin_recap = {k: v for k, v in core_config.JOB_SCHEDULES.items()
                 if k != "monthly_sales_recap"}
    monkeypatch.setattr(core_config, "JOB_SCHEDULES", sin_recap)
    assert bot._morning_sales_skip_hoy(datetime(2026, 8, 1, 8, 0)) is False


def test_catchup_y_deadman_no_esperan_morning_el_dia_1(bot):
    """El spec de morning_sales (compartido por catch-up y dead-man) NO lo
    considera pendiente el día 1 — sin 503 ni reintentos ese día."""
    specs = {k: due for k, _fn, due in bot._catchup_specs()}
    assert "morning_sales" in specs
    due = specs["morning_sales"]
    # Sábado 1 de agosto 2026, 9:00 (pasada la hora del reporte): NO due.
    assert due(datetime(2026, 8, 1, 9, 0)) is False
    # Lunes 3 de agosto 2026, 9:00: sí due.
    assert due(datetime(2026, 8, 3, 9, 0)) is True
    # El recap mensual SÍ es esperado el día 1.
    assert specs["monthly_sales_recap"](datetime(2026, 8, 1, 9, 0)) is True