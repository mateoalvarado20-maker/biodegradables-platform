"""Tests de la configuración central (Fase 5)."""
from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core_config


def test_feriados_cargados_para_este_anio_y_el_proximo():
    """Auditoría R8: faltaba 2027 — en enero los días hábiles se calculaban
    sin feriados, silenciosamente. Este test falla cada vez que se acerque
    un año sin cargar."""
    hoy = date.today()
    for year in (hoy.year, hoy.year + 1):
        assert core_config.EC_HOLIDAYS.get(year), (
            f"EC_HOLIDAYS no tiene {year} — agregar en core_config.py "
            "ANTES de que arranque el año"
        )


def test_holidays_for_anio_faltante_avisa_fuerte(capsys):
    core_config._warned_years.discard(1999)
    result = core_config.holidays_for(1999)
    assert result == set()
    err = capsys.readouterr().err
    assert "1999" in err and "SIN feriados" in err


def test_py_override_keyed_por_anio_y_mes():
    """Auditoría R8: el override de mayo estaba keyed solo por mes y se iba
    a re-aplicar en mayo 2027/2028."""
    assert core_config.py_override_for(2026, 5) == 38000.0
    assert core_config.py_override_for(2027, 5) is None  # NO se hereda


def test_destinatarios_overridables_por_env(monkeypatch):
    monkeypatch.setenv("REPORT_COMERCIAL_TO", "a@x.com, b@x.com")
    monkeypatch.setenv("REPORT_CC", "c@x.com")
    cc = importlib.reload(core_config)
    try:
        assert cc.JEFE == ["a@x.com", "b@x.com"]
        assert cc.MIO == "c@x.com"
    finally:
        monkeypatch.undo()
        importlib.reload(core_config)


def test_navidad_es_feriado_todos_los_anios():
    for year, days in core_config.EC_HOLIDAYS.items():
        assert date(year, 12, 25) in days
        assert date(year, 1, 1) in days
