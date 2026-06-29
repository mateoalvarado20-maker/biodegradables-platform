"""Tests de entrega confiable (Fase 3).

Cubre los requisitos explícitos del dueño del proyecto:
- un reporte nunca se envía dos veces (ledger),
- los reportes salen solo en los días/horas configurados,
- reintentos automáticos ante fallo + recuperación,
- el reporte comercial no sale con $0 cuando la fuente crítica falla.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    send_ledger = importlib.reload(send_ledger)
    assert str(send_ledger.LEDGER_PATH).startswith(str(tmp_path))
    return send_ledger


# ---------- Un reporte NUNCA se envía dos veces ----------

def test_claim_confirm_bloquea_segundo_envio(ledger):
    assert ledger.claim("morning_sales", "2026-06-12") is True
    ledger.confirm("morning_sales", "2026-06-12")
    # Segundo disparo el mismo día (retry, doble capa, re-enable accidental)
    assert ledger.claim("morning_sales", "2026-06-12") is False
    assert ledger.already_sent("morning_sales", "2026-06-12") is True


def test_claim_concurrente_solo_uno_gana(ledger):
    """N workers disparando el mismo reporte a la vez: exactamente 1 envía."""
    wins: list[bool] = []
    lock = threading.Lock()

    def worker():
        ok = ledger.claim("consolidated_daily", "2026-06-12")
        with lock:
            wins.append(ok)

    ts = [threading.Thread(target=worker) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert wins.count(True) == 1, f"{wins.count(True)} workers creyeron ganar el claim"


def test_release_permite_reintento_tras_fallo(ledger):
    assert ledger.claim("weekly_summaries", "2026-06-12") is True
    ledger.release("weekly_summaries", "2026-06-12")  # el envío falló
    assert ledger.claim("weekly_summaries", "2026-06-12") is True  # reintento OK


def test_dias_distintos_son_envios_distintos(ledger):
    ledger.claim("morning_sales", "2026-06-11")
    ledger.confirm("morning_sales", "2026-06-11")
    assert ledger.claim("morning_sales", "2026-06-12") is True


def test_claim_huerfano_expira(ledger, monkeypatch):
    """Proceso murió a mitad del envío: el claim expira y otro puede retomar."""
    assert ledger.claim("morning_sales", "2026-06-12") is True
    # Envejecer el claim manualmente más allá del TTL
    import safe_json
    data = safe_json.load_json(ledger.LEDGER_PATH, dict)
    data["entries"]["morning_sales:2026-06-12"]["claimed_at"] = "2026-06-12T00:00:00-05:00"
    safe_json.save_json(ledger.LEDGER_PATH, data)
    assert ledger.claim("morning_sales", "2026-06-12") is True


# ---------- Horarios y días configurados ----------

@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import safe_json
    importlib.reload(safe_json)
    import send_ledger
    importlib.reload(send_ledger)
    import teams_bot
    return importlib.reload(teams_bot)


def test_horarios_de_jobs_configurados(bot):
    """Los crons registrados coinciden EXACTAMENTE con lo acordado con el
    negocio. Si alguien mueve un horario sin querer, este test lo detecta."""
    bot._schedule_jobs()
    jobs = {j.id: str(j.trigger) for j in bot.scheduler.get_jobs()}

    esperados = {
        "checkin_weekday": ("day_of_week='mon-fri'", "hour='16'", "minute='30'"),
        "checkin_sucursales_weekday": ("day_of_week='mon-fri'", "hour='17'", "minute='10'"),
        "checkin_saturday": ("day_of_week='sat'", "hour='12'", "minute='30'"),
        "deliver_reminders": ("minute='*/5'",),
        "auto_assign_cobranzas": ("day_of_week='mon-fri'", "hour='7'", "minute='30'"),
        # weekly_summaries DESHABILITADO 2026-06-29 (ya no se agenda)
        "daily_news_brief": ("hour='6'", "minute='0'"),
        "monthly_sales_recap_day1": ("day='1'", "hour='9'"),
        "monthly_activities_recap_day1": ("day='1'", "hour='10'"),
        "apertura_caja_matinal": ("day_of_week='mon-fri'", "hour='8'", "minute='15'"),
        "consolidated_daily_summary": ("day_of_week='mon-fri'", "hour='18'", "minute='30'"),
        "saturday_recap": ("day_of_week='mon'", "hour='8'", "minute='0'"),
        "morning_sales_report": ("day_of_week='mon-sat'", "hour='8'", "minute='0'"),
    }
    for job_id, fragmentos in esperados.items():
        assert job_id in jobs, f"job {job_id} NO está registrado"
        for frag in fragmentos:
            assert frag in jobs[job_id], (
                f"{job_id}: esperaba {frag} en el trigger, got {jobs[job_id]}"
            )
    # El scheduler completo usa America/Guayaquil (no UTC, no offset naive)
    assert str(bot.scheduler.timezone) == "America/Guayaquil"
    # El job legacy 17:00 NO debe existir — quedó unificado en
    # checkin_sucursales_weekday (un solo scheduler por grupo, sin conflictos)
    assert "checkin_info_weekday" not in jobs
    # weekly_summaries DESHABILITADO 2026-06-29 — no debe estar agendado
    assert "weekly_summaries" not in jobs


def test_ningun_checkin_corre_domingo(bot):
    """Requisito de negocio 2026-06-12: domingos NO se envía ningún card."""
    import core_config
    from datetime import date as _d
    bot._schedule_jobs()
    for j in bot.scheduler.get_jobs():
        if not j.id.startswith("checkin"):
            continue
        trig = str(j.trigger)
        if "day_of_week" in trig:
            assert "sun" not in trig, f"{j.id} cubre domingo: {trig}"
            assert "mon-fri" in trig or "sat" in trig
    # Los overrides puntuales tampoco pueden caer en domingo
    for fecha_iso in core_config.CHECKIN_DATE_OVERRIDES:
        assert _d.fromisoformat(fecha_iso).weekday() != 6, (
            f"override {fecha_iso} cae domingo"
        )


def test_checkin_destinatarios_por_grupo(bot):
    """Lun-Vie 16:30 → Mateo+Gabriela S.; 17:10 y Sáb 12:30 → info@+quito@."""
    import core_config
    assert set(core_config.CHECKIN_OFICINA) == {
        "malvarado@biodegradablesecuador.com",
        "gsanchez@biodegradablesecuador.com",
    }
    assert set(core_config.CHECKIN_SUCURSALES) == {
        "info@biodegradablesecuador.com",
        "quito@biodegradablesecuador.com",
    }
    assert core_config.CHECKIN_WEEKDAY_OFICINA == (16, 30)
    assert core_config.CHECKIN_WEEKDAY_SUCURSALES == (17, 10)
    assert core_config.CHECKIN_SATURDAY_SUCURSALES == (12, 30)


def test_checkin_override_solo_fechas_vigentes(bot, monkeypatch):
    """Las fechas pasadas de CHECKIN_DATE_OVERRIDES no registran jobs; las
    de hoy en adelante sí (uno por horario)."""
    import core_config
    import send_ledger
    hoy = send_ledger.today_iso()
    monkeypatch.setattr(core_config, "CHECKIN_DATE_OVERRIDES", {
        "2000-01-03": [((9, 0), ["x@biodegradablesecuador.com"])],   # pasado
        hoy: [
            ((16, 45), ["info@biodegradablesecuador.com"]),
            ((16, 50), ["quito@biodegradablesecuador.com"]),
        ],
    })
    bot._schedule_jobs()
    ids = {j.id for j in bot.scheduler.get_jobs()}
    assert f"checkin_override_{hoy}_1645" in ids
    assert f"checkin_override_{hoy}_1650" in ids
    assert not any("2000-01-03" in i for i in ids)


def test_job_regular_omite_usuarios_con_override_hoy(bot, monkeypatch):
    """El día de un override, el job regular de sucursales NO les envía
    (evita card doble); el job de oficina sigue normal."""
    import asyncio
    import core_config
    import send_ledger
    hoy = send_ledger.today_iso()
    monkeypatch.setattr(core_config, "CHECKIN_DATE_OVERRIDES", {
        hoy: [
            ((16, 45), ["info@biodegradablesecuador.com"]),
            ((16, 50), ["quito@biodegradablesecuador.com"]),
        ],
    })
    llamadas = []

    async def fake_checkin(only=None, exclude=None):
        llamadas.append(set(only or ()))

    monkeypatch.setattr(bot, "send_daily_checkin", fake_checkin)
    asyncio.run(bot._job_checkin_sucursales())
    assert llamadas == []  # ambos con override → el regular no envía nada
    asyncio.run(bot._job_checkin_oficina())
    assert llamadas == [{
        "malvarado@biodegradablesecuador.com",
        "gsanchez@biodegradablesecuador.com",
    }]


def test_checkin_ledger_anti_duplicado(bot, monkeypatch):
    """Disparar el mismo job de check-in dos veces el mismo día solo envía
    una vez (ledger Fase 3)."""
    import asyncio
    llamadas = []

    async def fake_checkin(only=None, exclude=None):
        llamadas.append(set(only or ()))

    monkeypatch.setattr(bot, "send_daily_checkin", fake_checkin)
    asyncio.run(bot._job_checkin_oficina())
    asyncio.run(bot._job_checkin_oficina())  # mismo día → skip por ledger
    assert len(llamadas) == 1
    # El override usa su propia key — no choca con el job regular
    asyncio.run(bot._job_checkin_override(
        "1645", ["info@biodegradablesecuador.com"]
    ))
    asyncio.run(bot._job_checkin_override(
        "1645", ["info@biodegradablesecuador.com"]
    ))
    assert len(llamadas) == 2


def test_misfire_grace_y_coalesce_configurados(bot):
    """Auditoría S2: el default de 1s perdía ejecuciones en cada deploy."""
    defaults = bot.scheduler._job_defaults
    assert defaults["misfire_grace_time"] >= 600
    assert defaults["coalesce"] is True


def test_catchup_respeta_dias_y_horas(bot, monkeypatch):
    """El catch-up solo considera reportes cuyo día/hora YA pasó."""
    specs = {k: due for k, _fn, due in bot._catchup_specs()}

    martes_0730 = datetime(2026, 6, 9, 7, 30)   # antes de las 8:00
    martes_0900 = datetime(2026, 6, 9, 9, 0)    # después
    domingo_0900 = datetime(2026, 6, 14, 9, 0)  # domingo

    assert specs["morning_sales"](martes_0730) is False
    assert specs["morning_sales"](martes_0900) is True
    assert specs["morning_sales"](domingo_0900) is False
    # weekly_summaries DESHABILITADO 2026-06-29 — ya no está en el catch-up
    assert "weekly_summaries" not in specs
    assert specs["consolidated_daily"](datetime(2026, 6, 9, 18, 29)) is False
    assert specs["consolidated_daily"](datetime(2026, 6, 9, 18, 31)) is True
    # Recap del sábado: solo lunes desde las 8:00.
    lunes_0759 = datetime(2026, 6, 15, 7, 59)   # lunes antes de las 8
    lunes_0800 = datetime(2026, 6, 15, 8, 0)    # lunes 8:00
    martes_0900 = datetime(2026, 6, 16, 9, 0)   # martes (no es lunes)
    sabado_0900 = datetime(2026, 6, 13, 9, 0)   # sábado (no es lunes)
    assert specs["saturday_recap"](lunes_0759) is False
    assert specs["saturday_recap"](lunes_0800) is True
    assert specs["saturday_recap"](martes_0900) is False
    assert specs["saturday_recap"](sabado_0900) is False


# ---------- Retry + recuperación ante fallos ----------

def test_reliable_job_reintenta_y_recupera(bot, monkeypatch):
    intentos = {"n": 0}

    async def flaky():
        intentos["n"] += 1
        if intentos["n"] < 3:
            raise RuntimeError("fallo transitorio")

    alertas: list[str] = []
    monkeypatch.setattr(
        bot, "_send_job_failure_alert",
        lambda name, err, att: alertas.append(name),
    )
    ok = asyncio.run(
        bot._reliable_job("test_job", flaky, ledger_key="test_job", wait=0)
    )
    assert ok is True
    assert intentos["n"] == 3  # recuperó al tercer intento
    assert alertas == []       # sin alerta porque terminó OK
    import send_ledger
    assert send_ledger.already_sent("test_job") is True


def test_reliable_job_agota_reintentos_y_alerta(bot, monkeypatch):
    async def siempre_falla():
        raise RuntimeError("caído")

    alertas: list[tuple] = []
    monkeypatch.setattr(
        bot, "_send_job_failure_alert",
        lambda name, err, att: alertas.append((name, err)),
    )
    ok = asyncio.run(
        bot._reliable_job("job_roto", siempre_falla, ledger_key="job_roto", wait=0)
    )
    assert ok is False
    assert len(alertas) == 1           # un humano SE ENTERA
    assert "caído" in alertas[0][1]
    import send_ledger
    # El claim se liberó: un disparo manual posterior puede enviar
    assert send_ledger.claim("job_roto") is True


def test_reliable_job_no_duplica_si_ya_salio(bot):
    ejecuciones = {"n": 0}

    async def envia():
        ejecuciones["n"] += 1

    asyncio.run(bot._reliable_job("rep", envia, ledger_key="rep"))
    asyncio.run(bot._reliable_job("rep", envia, ledger_key="rep"))  # 2do disparo
    assert ejecuciones["n"] == 1  # NUNCA dos veces


# ---------- El reporte comercial no miente con $0 ----------

def test_daily_report_no_envia_con_fuente_critica_caida(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import daily_report
    daily_report = importlib.reload(daily_report)

    # Simular Contifico caído en las fuentes críticas
    def boom(*a, **k):
        raise RuntimeError("Contifico 500")

    monkeypatch.setattr(daily_report.contifico_client, "cumplimiento_mes", boom)
    monkeypatch.setattr(daily_report.contifico_client, "ventas_dia", boom)

    enviados: list = []
    monkeypatch.setattr(daily_report, "send_email", lambda *a, **k: enviados.append(a))
    monkeypatch.setattr(
        daily_report, "html_morning",
        lambda: (daily_report.q_ventas_mes(), daily_report.q_ventas_ayer(), "<html><body>x</body></html>")[-1],
    )
    monkeypatch.setattr(sys, "argv", ["daily_report", "morning"])
    monkeypatch.setattr(daily_report, "HUBSPOT_OK", False, raising=False)

    # Lunes-viernes: el guard de domingo no aplica en test si hoy es domingo;
    # forzamos modo test-morning que no tiene skip.
    monkeypatch.setattr(sys, "argv", ["daily_report", "test-morning"])
    with pytest.raises(RuntimeError, match="datos críticos"):
        daily_report.main()
    assert enviados == []  # el correo con $0 NO salió


def test_daily_report_banner_con_fuente_secundaria_caida(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    import daily_report
    daily_report = importlib.reload(daily_report)
    daily_report._FALLOS.append("KPIs de cartera (Contifico): RuntimeError: 500")
    html = daily_report._inject_warning_banner(
        "<html><body><p>reporte</p></body></html>", daily_report._FALLOS
    )
    assert "Datos parciales" in html
    assert "KPIs de cartera" in html
    assert html.index("Datos parciales") < html.index("reporte")
