"""Tests F2.2 (VER-IA 2026-07-02): horarios de jobs y timezone por tenant.

Propiedad central: core_config.JOB_SCHEDULES es la ÚNICA fuente de cuándo
corre cada job — el cron (_cron_for), el catch-up (_due_after) y el dead-man
(/health/deliveries vía _missing_deliveries) leen de ahí. Cambiar un horario
en el YAML del tenant mueve las tres superficies a la vez.
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime
from types import SimpleNamespace
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


def _entry(time, days=None, day_of_month=None):
    return SimpleNamespace(time=time, days=days, day_of_month=day_of_month)


# ---------- Defaults congelados (mismos horarios que producción) ----------

def test_defaults_espejan_horarios_de_produccion():
    import core_config
    importlib.reload(core_config)
    s = core_config.JOB_SCHEDULES
    assert s["morning_sales"] == {"time": (8, 0), "days": "mon-sat"}
    assert s["auto_assign_cobranzas"] == {"time": (7, 30), "days": "mon-fri"}
    assert s["consolidated_daily"] == {"time": (18, 30), "days": "mon-fri"}
    assert s["saturday_recap"] == {"time": (8, 0), "days": "mon"}
    assert s["daily_news_brief"] == {"time": (6, 0), "days": "daily"}
    assert s["monthly_sales_recap"] == {"time": (8, 0), "day_of_month": 1}
    assert core_config.TIMEZONE_NAME == "America/Guayaquil"


# ---------- Overrides del YAML (fail-closed) ----------

def test_override_valido_se_aplica():
    import core_config
    core_config = importlib.reload(core_config)
    core_config._apply_schedule_overrides({
        "morning_sales": _entry("07:15", days="mon-fri"),
        "monthly_sales_recap": _entry("09:00", day_of_month=2),
    })
    assert core_config.JOB_SCHEDULES["morning_sales"] == {
        "time": (7, 15), "days": "mon-fri",
    }
    assert core_config.JOB_SCHEDULES["monthly_sales_recap"] == {
        "time": (9, 0), "day_of_month": 2,
    }


def test_clave_desconocida_detiene_el_arranque():
    import core_config
    core_config = importlib.reload(core_config)
    with pytest.raises(ValueError, match="no es un job conocido"):
        core_config._apply_schedule_overrides({"job_inventado": _entry("08:00")})


def test_days_en_job_mensual_es_error():
    import core_config
    core_config = importlib.reload(core_config)
    with pytest.raises(ValueError, match="no acepta days"):
        core_config._apply_schedule_overrides(
            {"monthly_sales_recap": _entry("08:00", days="mon-fri")}
        )


def test_day_of_month_en_job_diario_es_error():
    import core_config
    core_config = importlib.reload(core_config)
    with pytest.raises(ValueError, match="no acepta day_of_month"):
        core_config._apply_schedule_overrides(
            {"morning_sales": _entry("08:00", day_of_month=5)}
        )


def test_schema_valida_formato_de_hora():
    from core.config.schema import JobSchedule
    with pytest.raises(Exception, match="time inválido"):
        JobSchedule(time="25:00")
    with pytest.raises(Exception):
        JobSchedule(time="0800")
    assert JobSchedule(time="07:30").time == "07:30"


# ---------- El switch yaml carga schedules + timezone ----------

def test_yaml_switch_carga_schedules_equivalentes(monkeypatch):
    """El bloque schedules: de tenants/biodegradables/config.yaml espeja los
    defaults — encender el switch no cambia ningún horario del tenant #1."""
    import core_config
    monkeypatch.setenv("TENANT_CONFIG_SOURCE", "yaml")
    monkeypatch.setenv("TENANT_SLUG", "biodegradables")
    cc = importlib.reload(core_config)
    legacy = importlib.reload  # solo para claridad
    assert cc.TIMEZONE_NAME == "America/Guayaquil"
    assert cc.JOB_SCHEDULES["morning_sales"] == {"time": (8, 0), "days": "mon-sat"}
    assert cc.JOB_SCHEDULES["consolidated_daily"] == {
        "time": (18, 30), "days": "mon-fri",
    }
    monkeypatch.delenv("TENANT_CONFIG_SOURCE")
    monkeypatch.delenv("TENANT_SLUG")
    importlib.reload(core_config)


# ---------- Las 3 superficies leen la MISMA fuente ----------

def test_cron_catchup_y_deadman_se_mueven_juntos(bot, monkeypatch):
    """Cambiar el horario de morning_sales mueve el cron registrado, la
    condición del catch-up y el dead-man — sin tocar teams_bot."""
    import core_config
    monkeypatch.setitem(
        core_config.JOB_SCHEDULES, "morning_sales",
        {"time": (9, 30), "days": "mon-fri"},
    )
    # 1) Cron
    bot._schedule_jobs()
    trig = str({j.id: j for j in bot.scheduler.get_jobs()}["morning_sales_report"].trigger)
    assert "hour='9'" in trig and "minute='30'" in trig and "mon-fri" in trig
    # 2) Catch-up
    specs = {k: due for k, _fn, due in bot._catchup_specs()}
    assert specs["morning_sales"](datetime(2026, 7, 6, 9, 29)) is False   # lunes
    assert specs["morning_sales"](datetime(2026, 7, 6, 9, 31)) is True
    assert specs["morning_sales"](datetime(2026, 7, 11, 10, 0)) is False  # sábado ya no
    # 3) Dead-man (a las 9:00 el reporte de 9:30 aún no puede faltar)
    lunes_0900 = bot.EC_TZ.localize(datetime(2026, 7, 6, 9, 0))
    assert "morning_sales" not in bot._missing_deliveries(lunes_0900)
    lunes_1015 = bot.EC_TZ.localize(datetime(2026, 7, 6, 10, 15))
    assert "morning_sales" in bot._missing_deliveries(lunes_1015)


def test_dow_match(bot):
    lunes = datetime(2026, 7, 6)
    sabado = datetime(2026, 7, 11)
    domingo = datetime(2026, 7, 5)
    assert bot._dow_match(lunes, "mon-fri") is True
    assert bot._dow_match(sabado, "mon-fri") is False
    assert bot._dow_match(sabado, "mon-sat") is True
    assert bot._dow_match(domingo, "daily") is True
    assert bot._dow_match(lunes, "mon") is True
    assert bot._dow_match(sabado, "mon,wed") is False


def test_trigger_mensual_desde_schedules(bot):
    bot._schedule_jobs()
    jobs = {j.id: str(j.trigger) for j in bot.scheduler.get_jobs()}
    assert "day='1'" in jobs["monthly_sales_recap_day1"]
    assert "hour='8'" in jobs["monthly_sales_recap_day1"]


# ---------- Timezone del tenant ----------

def test_timezone_de_env_llega_al_scheduler(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("TENANT_TIMEZONE", "America/Bogota")
    import safe_json, send_ledger, core_config
    importlib.reload(safe_json)
    importlib.reload(send_ledger)
    importlib.reload(core_config)
    import teams_bot
    bot = importlib.reload(teams_bot)
    assert str(bot.EC_TZ) == "America/Bogota"
    assert str(bot.scheduler.timezone) == "America/Bogota"
    # limpiar para no contaminar otros tests del proceso
    monkeypatch.delenv("TENANT_TIMEZONE")
    importlib.reload(core_config)
    importlib.reload(teams_bot)
