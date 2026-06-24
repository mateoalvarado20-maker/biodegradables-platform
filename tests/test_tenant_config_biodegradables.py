"""RED DE SEGURIDAD de la migración: la config externalizada == los valores que
hoy usan los bots.

Garantiza que mover los literales de Biodegradables a
`tenants/biodegradables/config.yaml` no cambió ningún valor respecto de
`core_config.py`. Hasta que esto pase en verde, el switch de core_config a
leer-desde-YAML NO debe hacerse.
"""
from __future__ import annotations

import core_config as cc

from core.config.schema import parse_hhmm
from core.context import TenantContext


def _cfg():
    return TenantContext.load("biodegradables").config


def test_recipients_match():
    c = _cfg()
    assert c.recipients.commercial_report == cc.JEFE
    assert c.recipients.commercial_report_cc == [cc.MIO]
    assert c.recipients.logistics_report == [cc.GABRIELA]
    assert c.recipients.calendar_sync_users == cc.CALENDAR_SYNC_USERS


def test_commercial_match():
    c = _cfg()
    assert c.commercial.meta_factor == cc.META_FACTOR
    assert c.py_override_map() == cc.PY_OVERRIDE
    t = c.commercial.thresholds
    assert t.cumpl_verde == cc.CUMPL_VERDE
    assert t.cumpl_amarillo == cc.CUMPL_AMARILLO
    assert t.ayer_verde == cc.AYER_VERDE
    assert t.ayer_amarillo == cc.AYER_AMARILLO
    assert t.mora_verde == cc.MORA_VERDE
    assert t.mora_amarillo == cc.MORA_AMARILLO


def test_checkin_match():
    c = _cfg()
    assert c.checkin.oficina.users == cc.CHECKIN_OFICINA
    assert parse_hhmm(c.checkin.oficina.weekday_time) == cc.CHECKIN_WEEKDAY_OFICINA
    assert c.checkin.sucursales.users == cc.CHECKIN_SUCURSALES
    assert parse_hhmm(c.checkin.sucursales.weekday_time) == cc.CHECKIN_WEEKDAY_SUCURSALES
    assert parse_hhmm(c.checkin.sucursales.saturday_time) == cc.CHECKIN_SATURDAY_SUCURSALES


def test_holidays_match():
    c = _cfg()
    for year in (2025, 2026, 2027):
        assert set(c.holidays[year]) == cc.holidays_for(year)


def test_company_match():
    c = _cfg()
    assert c.display_name == cc.COMPANY_NAME
    assert c.company.sector == cc.COMPANY_SECTOR
    assert c.company.sucursales_desc == cc.COMPANY_SUCURSALES_DESC
    assert c.company.sucursal_names == cc.SUCURSAL_NAMES


def test_people_match():
    """El directorio del YAML reproduce exactamente core_config.PEOPLE legacy."""
    c = _cfg()
    from_yaml = cc._normalize_people({
        p.email: {
            "name": p.name, "role": p.role, "sucursal": p.sucursal,
            "asistente_num": p.asistente_num, "supervisor": p.supervisor,
            "rotativo_sabado": p.rotativo_sabado,
        }
        for p in c.people
    })
    assert from_yaml == cc.PEOPLE
    # Y los derivados (lo que consumen los bots) también coinciden.
    assert {e: cc.display_name_for(e) for e in cc.PEOPLE} == cc.EMAIL_TO_NAME
