"""Tests F2.3 (VER-IA 2026-07-02): módulos activables por tenant.

Propiedad central: apagar un módulo en el config.yaml del tenant quita a la
vez (1) sus jobs del scheduler, (2) su participación en el catch-up y en el
dead-man (/health/deliveries), y (3) sus tools de los bots. Biodegradables
tiene el catálogo completo encendido — cero cambio de comportamiento.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    importlib.reload(send_ledger)
    import core_config
    importlib.reload(core_config)
    import teams_bot
    return importlib.reload(teams_bot)


def _job_ids(bot) -> set[str]:
    bot._schedule_jobs()
    return {j.id for j in bot.scheduler.get_jobs()}


def _spec_keys(bot) -> set[str]:
    return {k for k, _fn, _due in bot._catchup_specs()}


# ---------- Catálogo y defaults ----------

def test_catalogo_coincide_entre_core_config_y_schema():
    """MODULES (core_config, sin deps) y KNOWN_MODULES (schema pydantic)
    viven duplicados por diseño — este test los mantiene idénticos."""
    import core_config
    importlib.reload(core_config)
    from core.config.schema import KNOWN_MODULES
    assert set(core_config.MODULES) == set(KNOWN_MODULES)


def test_default_todo_encendido():
    import core_config
    importlib.reload(core_config)
    assert all(core_config.MODULES.values())


def test_modulo_desconocido_detiene_el_arranque():
    import core_config
    core_config = importlib.reload(core_config)
    with pytest.raises(ValueError, match="no es un módulo conocido"):
        core_config._apply_module_overrides({"inventado": True})


def test_schema_rechaza_modulo_desconocido():
    from core.config.schema import TenantConfig
    with pytest.raises(Exception, match="módulos desconocidos"):
        TenantConfig(slug="x", display_name="X", modules={"tiktok": True})


def test_yaml_biodegradables_todo_encendido(monkeypatch):
    import core_config
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "biodegradables")
    cc = importlib.reload(core_config)
    assert all(cc.MODULES.values())
    monkeypatch.delenv("TENANT_CONFIG_SOURCE")
    monkeypatch.delenv("TENANT_SLUG")
    importlib.reload(core_config)


# ---------- Con todo encendido: el registro actual no cambia ----------

def test_catalogo_completo_registra_los_jobs_de_siempre(bot):
    ids = _job_ids(bot)
    for jid in ("checkin_weekday", "checkin_sucursales_weekday", "checkin_saturday",
                "deliver_reminders", "auto_assign_cobranzas", "task_confirmations",
                "daily_news_brief", "monthly_sales_recap_day1",
                "monthly_activities_recap_day1", "apertura_caja_matinal",
                "consolidated_daily_summary", "saturday_recap",
                "jose_asistencia_weekday", "jose_asistencia_saturday",
                "morning_sales_report", "catchup_retry"):
        assert jid in ids, f"{jid} debería estar registrado con el catálogo completo"


# ---------- Apagar un módulo quita jobs + catch-up + dead-man ----------

def test_sin_cobranzas_no_hay_job_ni_catchup_ni_deadman(bot, monkeypatch):
    import core_config
    monkeypatch.setitem(core_config.MODULES, "cobranzas", False)
    assert "auto_assign_cobranzas" not in _job_ids(bot)
    assert "auto_assign_cobranzas" not in _spec_keys(bot)
    lunes_0900 = bot.EC_TZ.localize(datetime(2026, 7, 6, 9, 0))
    assert "auto_assign_cobranzas" not in bot._missing_deliveries(lunes_0900)


def test_sin_chofer_no_hay_cards_de_asistencia(bot, monkeypatch):
    import core_config
    monkeypatch.setitem(core_config.MODULES, "chofer", False)
    ids = _job_ids(bot)
    assert "jose_asistencia_weekday" not in ids
    assert "jose_asistencia_saturday" not in ids
    assert "jose_asistencia" not in _spec_keys(bot)


def test_sin_commercial_no_hay_reporte_ni_recap_de_ventas(bot, monkeypatch):
    import core_config
    monkeypatch.setitem(core_config.MODULES, "commercial", False)
    ids = _job_ids(bot)
    assert "morning_sales_report" not in ids
    assert "monthly_sales_recap_day1" not in ids
    assert "morning_sales" not in _spec_keys(bot)
    # El resto sigue vivo
    assert "checkin_weekday" in ids


def test_sin_activities_cae_todo_el_cluster_de_equipo(bot, monkeypatch):
    import core_config
    monkeypatch.setitem(core_config.MODULES, "activities", False)
    ids = _job_ids(bot)
    for jid in ("checkin_weekday", "checkin_sucursales_weekday", "checkin_saturday",
                "deliver_reminders", "task_confirmations", "consolidated_daily_summary",
                "saturday_recap", "apertura_caja_matinal",
                "monthly_activities_recap_day1",
                # dependientes: cobranzas y chofer requieren activities
                "auto_assign_cobranzas", "jose_asistencia_weekday"):
        assert jid not in ids, f"{jid} no debería registrarse sin activities"
    # El comercial es independiente
    assert "morning_sales_report" in ids
    assert "daily_news_brief" in ids


def test_sin_news_brief_no_hay_job_nocturno(bot, monkeypatch):
    import core_config
    monkeypatch.setitem(core_config.MODULES, "news_brief", False)
    assert "daily_news_brief" not in _job_ids(bot)
    assert "daily_news_brief" not in _spec_keys(bot)


# ---------- Tools de los bots ----------

def test_tools_de_modulo_apagado_desaparecen_del_data_bot(bot, monkeypatch):
    import core_config
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    nombres = {t["name"] for t in ask_agent._tools_for_mode("data")}
    assert "get_saldos_pendientes_clientes" in nombres
    assert "get_hubspot_leads_ayer" in nombres

    monkeypatch.setitem(core_config.MODULES, "cobranzas", False)
    monkeypatch.setitem(core_config.MODULES, "marketing", False)
    nombres = {t["name"] for t in ask_agent._tools_for_mode("data")}
    assert "get_saldos_pendientes_clientes" not in nombres
    assert "get_hubspot_leads_ayer" not in nombres
    # Las de ventas siguen (commercial encendido)
    assert "get_ventas_dia" in nombres


def test_tools_activities_respetan_modulo(bot, monkeypatch):
    import core_config
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    supervisor = next(iter(ask_agent.SUPERVISORS_ONLY_EMAILS))
    nombres = {t["name"] for t in ask_agent._tools_for_mode("activities", supervisor)}
    assert "add_activity_for_collaborator" in nombres

    monkeypatch.setitem(core_config.MODULES, "activities", False)
    nombres = {t["name"] for t in ask_agent._tools_for_mode("activities", supervisor)}
    assert "add_activity_for_collaborator" not in nombres
    assert "mark_daily_activity" not in nombres


def test_nombres_de_module_tool_names_existen(bot):
    """Cada tool referenciada en MODULE_TOOL_NAMES existe en TOOLS — un rename
    de tool no puede dejar el gating apuntando al vacío."""
    import ask_agent
    ask_agent = importlib.reload(ask_agent)
    reales = {t["name"] for t in ask_agent.TOOLS}
    for mod, names in ask_agent.MODULE_TOOL_NAMES.items():
        faltan = names - reales
        assert not faltan, f"módulo {mod}: tools inexistentes {faltan}"
