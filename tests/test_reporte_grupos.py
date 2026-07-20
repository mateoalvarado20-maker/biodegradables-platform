"""Agrupación por equipo en el consolidado diario (2026-07-20): el bloque de
Mateo va bajo el encabezado "VER-IA" y el resto bajo el de la empresa. Sin
overrides configurados no hay encabezados (tenants sin agrupación, igual que
siempre)."""
from __future__ import annotations

import importlib
from datetime import date

import pytest

MATEO = "malvarado@biodegradablesecuador.com"
GABRIELA = "gsanchez@biodegradablesecuador.com"
_WED = date(2026, 7, 15)  # miércoles (evita ramas de sábado)


@pytest.fixture()
def tr(state_env, monkeypatch):
    import team_reports
    return importlib.reload(team_reports)


def _seed(a, email):
    a.add_adhoc(f"act-{email.split('@')[0]}", "Actividad X", user_email=email,
                tipo="diaria", meta=1, wk=a.week_key(_WED))


def test_mateo_bajo_veria_y_resto_bajo_empresa(tr, state_env):
    import core_config
    a = state_env.activity_state
    _seed(a, MATEO)
    _seed(a, GABRIELA)

    html = tr._consolidated_daily_summary_html([MATEO, GABRIELA], target_date=_WED)

    assert "VER-IA" in html
    assert core_config.COMPANY_NAME in html
    # La empresa va primero, VER-IA después; y el bloque de Mateo queda
    # DESPUÉS del encabezado VER-IA.
    i_empresa = html.find(f"🏢 {core_config.COMPANY_NAME}")
    i_veria = html.find("🏢 VER-IA")
    i_mateo = html.find("MATEO ALVARADO")
    assert 0 <= i_empresa < i_veria
    assert i_veria < i_mateo
    # El bloque de Gabriela queda dentro del grupo empresa (antes de VER-IA)
    i_gabriela = html.find("GABRIELA S")
    if i_gabriela == -1:
        i_gabriela = html.find("GABRIELA")
    assert i_empresa < i_gabriela < i_veria


def test_sin_overrides_no_hay_encabezados(tr, state_env, monkeypatch):
    import core_config
    monkeypatch.setattr(core_config, "REPORT_GROUP_OVERRIDES", {})
    a = state_env.activity_state
    _seed(a, MATEO)
    _seed(a, GABRIELA)

    html = tr._consolidated_daily_summary_html([MATEO, GABRIELA], target_date=_WED)
    assert "🏢" not in html  # un solo grupo → sin banners
    assert "VER-IA" not in html
