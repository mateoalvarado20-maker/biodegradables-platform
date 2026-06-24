"""Switch multiempresa de core_config: yaml == legacy, y default = legacy.

Es la red de seguridad del primer módulo que "se cablea": garantiza que prender
TENANT_CONFIG_SOURCE=yaml no cambia ningún valor respecto del comportamiento actual.
"""
from __future__ import annotations

import importlib


def _reload_core_config():
    import core_config

    return importlib.reload(core_config)


def test_default_is_legacy(monkeypatch):
    monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
    cc = _reload_core_config()
    assert "dsanchez@biodegradablesecuador.com" in cc.JEFE
    assert cc.META_FACTOR == 1.20
    assert cc.CHECKIN_WEEKDAY_OFICINA == (16, 30)


def test_yaml_switch_equals_legacy(monkeypatch):
    # 1) capturar valores legacy
    monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
    legacy = _reload_core_config()
    snap = {
        "JEFE": list(legacy.JEFE),
        "MIO": legacy.MIO,
        "GABRIELA": legacy.GABRIELA,
        "CALENDAR": list(legacy.CALENDAR_SYNC_USERS),
        "META": legacy.META_FACTOR,
        "PY": dict(legacy.PY_OVERRIDE),
        "OFI_T": legacy.CHECKIN_WEEKDAY_OFICINA,
        "SUC_T": legacy.CHECKIN_WEEKDAY_SUCURSALES,
        "SAB_T": legacy.CHECKIN_SATURDAY_SUCURSALES,
        "OFI_U": list(legacy.CHECKIN_OFICINA),
        "SUC_U": list(legacy.CHECKIN_SUCURSALES),
        "CUMPL_VERDE": legacy.CUMPL_VERDE,
        "MORA_AMARILLO": legacy.MORA_AMARILLO,
        "H2025": set(legacy.holidays_for(2025)),
        "H2026": set(legacy.holidays_for(2026)),
        "H2027": set(legacy.holidays_for(2027)),
        "COMPANY_NAME": legacy.COMPANY_NAME,
        "COMPANY_SECTOR": legacy.COMPANY_SECTOR,
        "PEOPLE": dict(legacy.PEOPLE),
        "EMAIL_TO_NAME": dict(legacy.EMAIL_TO_NAME),
        "SUPERVISORS": set(legacy.SUPERVISORS_ONLY_EMAILS),
        "ASISTENTES": set(legacy.ASISTENTE_EMAILS),
    }

    # 2) prender el flag y comparar
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    sw = _reload_core_config()
    try:
        assert list(sw.JEFE) == snap["JEFE"]
        assert sw.MIO == snap["MIO"]
        assert sw.GABRIELA == snap["GABRIELA"]
        assert list(sw.CALENDAR_SYNC_USERS) == snap["CALENDAR"]
        assert sw.META_FACTOR == snap["META"]
        assert dict(sw.PY_OVERRIDE) == snap["PY"]
        assert sw.CHECKIN_WEEKDAY_OFICINA == snap["OFI_T"]
        assert sw.CHECKIN_WEEKDAY_SUCURSALES == snap["SUC_T"]
        assert sw.CHECKIN_SATURDAY_SUCURSALES == snap["SAB_T"]
        assert list(sw.CHECKIN_OFICINA) == snap["OFI_U"]
        assert list(sw.CHECKIN_SUCURSALES) == snap["SUC_U"]
        assert sw.CUMPL_VERDE == snap["CUMPL_VERDE"]
        assert sw.MORA_AMARILLO == snap["MORA_AMARILLO"]
        assert set(sw.holidays_for(2025)) == snap["H2025"]
        assert set(sw.holidays_for(2026)) == snap["H2026"]
        assert set(sw.holidays_for(2027)) == snap["H2027"]
        assert sw.py_override_for(2026, 5) == legacy.py_override_for(2026, 5)
        # Identidad de empresa + directorio de personas (Fase 1)
        assert sw.COMPANY_NAME == snap["COMPANY_NAME"]
        assert sw.COMPANY_SECTOR == snap["COMPANY_SECTOR"]
        assert dict(sw.PEOPLE) == snap["PEOPLE"]
        assert dict(sw.EMAIL_TO_NAME) == snap["EMAIL_TO_NAME"]
        assert set(sw.SUPERVISORS_ONLY_EMAILS) == snap["SUPERVISORS"]
        assert set(sw.ASISTENTE_EMAILS) == snap["ASISTENTES"]
    finally:
        # 3) restaurar legacy para no contaminar otros tests
        monkeypatch.delenv("TENANT_CONFIG_SOURCE", raising=False)
        _reload_core_config()
