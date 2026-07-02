"""Tests de la Fase 0 VER-IA (2026-07-02): estabilización de críticos.

Cubre:
- ADMIN_API_TOKEN sin fallback al secret OAuth del bot (fail-closed).
- ALERT_EMAIL multi-destinatario + throttle de alertas (1 por job por día).
- Allowlist de remitentes en /admin/schedule-one-time-email.
- Jobs antes huérfanos ahora bajo _reliable_job y dentro del catch-up.
- Dead-man switch de entregas (_missing_deliveries → /health/deliveries).
- Gate de higiene async (sin red síncrona en corutinas).
"""
from __future__ import annotations

import asyncio
import importlib
import subprocess
import sys
import textwrap
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
    import teams_bot
    return importlib.reload(teams_bot)


# ---------- ADMIN_API_TOKEN sin fallback ----------

def test_admin_token_sin_fallback_al_password_del_bot(tmp_path, monkeypatch):
    """Con MICROSOFT_APP_PASSWORD seteado pero SIN ADMIN_API_TOKEN, el token
    admin queda VACÍO (fail-closed en _require_admin) — nunca cae al secret
    OAuth del bot (auditoría CRÍTICA-1)."""
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MICROSOFT_APP_PASSWORD", "secreto-oauth-del-bot")
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    import teams_bot
    bot = importlib.reload(teams_bot)
    assert bot.ADMIN_API_TOKEN == ""
    assert bot.DATA_APP_PWD == "secreto-oauth-del-bot"


def test_admin_token_desde_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_API_TOKEN", "  token-propio-de-admin  ")
    import teams_bot
    bot = importlib.reload(teams_bot)
    assert bot.ADMIN_API_TOKEN == "token-propio-de-admin"


# ---------- Alertas: multi-destinatario + throttle ----------

def test_alert_email_acepta_lista(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ALERT_EMAIL", "ops@ver-ia.com, dueno@cliente.com")
    import teams_bot
    bot = importlib.reload(teams_bot)
    assert bot.ALERT_EMAILS == ["ops@ver-ia.com", "dueno@cliente.com"]
    assert bot.ALERT_EMAIL == "ops@ver-ia.com"  # emisor = primero de la lista


def test_alerta_throttle_una_por_job_por_dia(bot, monkeypatch):
    """Un job recurrente caído (cada 5 min) manda UNA alerta al día, no 288."""
    import graph_mail
    envios: list[dict] = []
    monkeypatch.setattr(
        graph_mail, "send", lambda **kw: envios.append(kw), raising=False
    )
    bot._send_job_failure_alert("deliver_reminders", "boom", 2)
    bot._send_job_failure_alert("deliver_reminders", "boom otra vez", 2)
    assert len(envios) == 1
    # Jobs distintos alertan por separado el mismo día
    bot._send_job_failure_alert("otro_job", "boom", 3)
    assert len(envios) == 2
    # El destinatario es la lista completa
    assert envios[0]["to"] == bot.ALERT_EMAILS


def test_alerta_fallida_libera_el_throttle(bot, monkeypatch):
    """Si el envío de la alerta falla, el throttle se libera: el siguiente
    fallo del día SÍ puede alertar."""
    import graph_mail
    intentos = {"n": 0}

    def flaky_send(**kw):
        intentos["n"] += 1
        if intentos["n"] == 1:
            raise RuntimeError("Graph caído")

    monkeypatch.setattr(graph_mail, "send", flaky_send, raising=False)
    bot._send_job_failure_alert("job_x", "boom", 3)   # falla y libera
    bot._send_job_failure_alert("job_x", "boom", 3)   # reintenta y sale
    assert intentos["n"] == 2


# ---------- Allowlist de remitentes del endpoint admin de email ----------

def test_allowed_email_senders_es_gerencia_mas_operador(bot, monkeypatch):
    import core_config
    allowed = bot._allowed_email_senders()
    for e in [*core_config.JEFE, core_config.MIO]:
        assert e.lower() in allowed
    assert "cualquiera@biodegradablesecuador.com" not in allowed


def test_allowed_email_senders_ampliable_por_env(bot, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL_FROM_ALLOWLIST", "Extra@Cliente.com")
    assert "extra@cliente.com" in bot._allowed_email_senders()


# ---------- Jobs huérfanos: wrappers + catch-up ----------

def test_jobs_huerfanos_registrados_con_wrapper_confiable(bot):
    """Los 5 jobs de la auditoría H6 ya no se registran a pelo: el callable
    registrado es el wrapper _job_* que pasa por _reliable_job."""
    bot._schedule_jobs()
    jobs = {j.id: j.func for j in bot.scheduler.get_jobs()}
    assert jobs["deliver_reminders"] is bot._job_deliver_reminders
    assert jobs["auto_assign_cobranzas"] is bot._job_auto_assign_cobranzas
    assert jobs["daily_news_brief"] is bot._job_news_brief
    assert jobs["apertura_caja_matinal"] is bot._job_apertura_caja_matinal
    assert jobs["jose_asistencia_weekday"] is bot._job_jose_asistencia
    assert jobs["jose_asistencia_saturday"] is bot._job_jose_asistencia
    # Y el re-catch-up horario está agendado
    assert "catchup_retry" in jobs


def test_catchup_incluye_los_jobs_nuevos(bot):
    specs = {k: due for k, _fn, due in bot._catchup_specs()}
    for key in ("auto_assign_cobranzas", "daily_news_brief",
                "apertura_caja_matinal", "jose_asistencia"):
        assert key in specs, f"{key} no está en el catch-up"
    # Cobranzas: lun-vie desde 7:30
    assert specs["auto_assign_cobranzas"](datetime(2026, 7, 6, 7, 29)) is False
    assert specs["auto_assign_cobranzas"](datetime(2026, 7, 6, 7, 31)) is True
    assert specs["auto_assign_cobranzas"](datetime(2026, 7, 5, 9, 0)) is False  # domingo
    # José: lun-vie 17:10, sáb 12:30
    assert specs["jose_asistencia"](datetime(2026, 7, 6, 17, 9)) is False
    assert specs["jose_asistencia"](datetime(2026, 7, 6, 17, 10)) is True
    assert specs["jose_asistencia"](datetime(2026, 7, 11, 12, 30)) is True   # sábado
    assert specs["jose_asistencia"](datetime(2026, 7, 5, 13, 0)) is False    # domingo


def test_cobranzas_fallo_total_lanza_para_alertar(bot, monkeypatch):
    """Contifico caído en ambas ciudades → la corutina LANZA (para que
    _reliable_job reintente y alerte). Incidente 2026-06-23: antes terminaba
    'OK' con 0 asignadas y 0 avisos."""
    def boom(*a, **k):
        raise RuntimeError("Contifico 500")

    monkeypatch.setattr(bot.contifico_client, "cartera_vencida_por_ciudad", boom)
    monkeypatch.setattr(bot.contifico_client, "clientes_sin_credito_con_saldo", boom)
    with pytest.raises(RuntimeError, match="fallo total"):
        asyncio.run(bot.auto_assign_cobranzas())


def test_dia_sin_cobranzas_no_es_error(bot, monkeypatch):
    monkeypatch.setattr(
        bot.contifico_client, "cartera_vencida_por_ciudad", lambda *a, **k: []
    )
    monkeypatch.setattr(
        bot.contifico_client, "clientes_sin_credito_con_saldo", lambda *a, **k: []
    )
    asyncio.run(bot.auto_assign_cobranzas())  # no lanza


# ---------- Dead-man switch de entregas ----------

def test_missing_deliveries_detecta_reporte_no_enviado(bot):
    """Lunes 9:00 EC con ledger vacío → morning_sales (8:00) figura como
    faltante; tras confirmar todo en el ledger, la lista queda vacía."""
    import send_ledger
    lunes_0900 = bot.EC_TZ.localize(datetime(2026, 7, 6, 9, 0))
    faltantes = bot._missing_deliveries(lunes_0900)
    assert "morning_sales" in faltantes
    assert "auto_assign_cobranzas" in faltantes
    # El consolidado de 18:30 NO puede faltar a las 9:00
    assert "consolidated_daily" not in faltantes

    hoy = send_ledger.today_iso()
    for key in faltantes:
        send_ledger.claim(key, hoy)
        send_ledger.confirm(key, hoy)
    assert bot._missing_deliveries(lunes_0900) == []


def test_missing_deliveries_respeta_gracia(bot):
    """A las 8:15 el morning de 8:00 está dentro de los 30 min de gracia."""
    lunes_0815 = bot.EC_TZ.localize(datetime(2026, 7, 6, 8, 15))
    assert "morning_sales" not in bot._missing_deliveries(lunes_0815)


def test_missing_deliveries_domingo_solo_news_brief(bot):
    """Domingo no hay reportes ni cards — lo único agendado los 7 días es el
    news brief de las 6:00."""
    domingo = bot.EC_TZ.localize(datetime(2026, 7, 5, 12, 0))
    assert bot._missing_deliveries(domingo) == ["daily_news_brief"]


# ---------- Gate de higiene async ----------

def test_teams_bot_sin_red_sincrona_en_corutinas():
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_async_hygiene.py"),
         str(ROOT / "teams_bot.py")],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_checker_detecta_el_patron_del_incidente(tmp_path):
    """El gate reconoce el patrón exacto que causó los incidentes: llamada
    directa a graph_mail.send dentro de un async def."""
    malo = tmp_path / "malo.py"
    malo.write_text(textwrap.dedent("""
        import graph_mail

        async def job():
            graph_mail.send(to="x")           # ← violación

        async def job_ok():
            import asyncio
            await asyncio.to_thread(graph_mail.send, to="x")   # OK (referencia)
            await asyncio.to_thread(lambda: graph_mail.send(to="x"))  # OK (lambda)

        def sync_helper():
            graph_mail.send(to="x")           # OK: no corre en el loop
    """), encoding="utf-8")
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "check_async_hygiene.py"), str(malo)],
        capture_output=True, text=True,
    )
    assert res.returncode == 1
    assert res.stdout.count("graph_mail.send") == 1  # SOLO la línea mala
