"""Entrada principal del Azure Function App.

Cron jobs migrados desde la laptop a Azure Functions. Cada `@app.timer_trigger`
define un job programado que reemplaza una de las tareas de Task Scheduler de
Mateo.

Schedules en formato NCRONTAB: `segundos minutos horas día mes díaSemana`.
Las horas son UTC; Ecuador = UTC-5 (sin DST), así que 8 AM EC = 13:00 UTC.
"""
from __future__ import annotations

import logging
import sys
import traceback

import azure.functions as func

app = func.FunctionApp()


@app.timer_trigger(
    schedule="0 0 13 * * *",  # 13:00 UTC = 8:00 AM Ecuador
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
def logistics_morning(myTimer: func.TimerRequest) -> None:
    """Reporte diario de logística a Gabriela.
    Reemplaza la schtask BiodegradablesEcuador-LogisticsReport-Morning.
    """
    logging.info("logistics_morning: timer fired")
    sys.argv = ["function_app", "morning"]
    try:
        from daily_logistics_report import main
        result = main()
        logging.info(f"logistics_morning: completed with exit={result}")
    except Exception as e:
        logging.error(f"logistics_morning FAILED: {e}")
        logging.error(traceback.format_exc())
        raise


@app.timer_trigger(
    schedule="0 0 13 * * *",  # 13:00 UTC = 8:00 AM Ecuador
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
def morning_sales_report(myTimer: func.TimerRequest) -> None:
    """Reporte comercial diario (ventas + Power BI + cartera + HubSpot) a Daniel/Gabriela.
    Reemplaza la schtask BiodegradablesEcuador-DailyReport-Morning de la PC de Mateo.
    """
    logging.info("morning_sales_report: timer fired")
    sys.argv = ["function_app", "morning"]
    try:
        from daily_report import main
        result = main()
        logging.info(f"morning_sales_report: completed with exit={result}")
    except Exception as e:
        logging.error(f"morning_sales_report FAILED: {e}")
        logging.error(traceback.format_exc())
        raise


@app.timer_trigger(
    schedule="0 */15 * * * *",  # cada 15 minutos
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
def reply_agent_tick(myTimer: func.TimerRequest) -> None:
    """Reply agent: lee inbox de Mateo, enriquece con Apollo, crea borradores.
    Reemplaza la schtask BiodegradablesEcuador-ReplyAgent-15min.
    """
    logging.info("reply_agent_tick: timer fired")
    sys.argv = ["function_app", "--since-hours", "1"]
    try:
        from reply_agent import main
        result = main()
        logging.info(f"reply_agent_tick: completed with exit={result}")
    except Exception as e:
        logging.error(f"reply_agent_tick FAILED: {e}")
        logging.error(traceback.format_exc())
        raise


@app.timer_trigger(
    schedule="0 */30 * * * *",  # cada 30 minutos
    arg_name="myTimer",
    run_on_startup=False,
    use_monitor=True,
)
def apollo_orchestrator_tick(myTimer: func.TimerRequest) -> None:
    """Tick del orquestador Apollo cada 30 minutos.
    Internamente respeta horario hábil lun-vie 9-18 EC y feriados Ecuador.
    Reemplaza la schtask BiodegradablesEcuador-ApolloOrchestrator-30min.
    """
    logging.info("apollo_orchestrator_tick: timer fired")
    sys.argv = ["function_app"]  # sin args = modo tick normal
    try:
        from apollo_orchestrator import main
        result = main()
        logging.info(f"apollo_orchestrator_tick: completed with exit={result}")
    except Exception as e:
        logging.error(f"apollo_orchestrator_tick FAILED: {e}")
        logging.error(traceback.format_exc())
        raise


# ============== TEAMS BOT ==============
@app.route(route="messages", auth_level=func.AuthLevel.ANONYMOUS, methods=["POST"])
async def bot_messages(req: func.HttpRequest) -> func.HttpResponse:
    """Endpoint que recibe los activities del Azure Bot (canal Teams).
    Llamado por Bot Framework, autenticado vía el header Authorization."""
    try:
        body = req.get_json()
    except Exception:
        return func.HttpResponse("Body inválido", status_code=400)

    auth_header = req.headers.get("authorization", "")
    try:
        from bot_handler import process_activity
        await process_activity(body, auth_header)
        return func.HttpResponse(status_code=200)
    except Exception as e:
        logging.error(f"bot_messages FAILED: {e}")
        logging.error(traceback.format_exc())
        return func.HttpResponse(f"Error: {e}", status_code=500)


# Endpoint HTTP para disparar manualmente el reporte (para pruebas)
@app.route(route="trigger/logistics", auth_level=func.AuthLevel.FUNCTION)
def trigger_logistics(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/trigger/logistics?code=<function-key>
    Dispara el reporte logística como si fuera la corrida de las 8 AM.
    Útil para test después del deploy.
    """
    logging.info("trigger_logistics: HTTP trigger fired")
    sys.argv = ["function_app", "morning"]
    try:
        from daily_logistics_report import main
        result = main()
        return func.HttpResponse(
            f"Reporte de logística ejecutado. Exit code: {result}",
            status_code=200,
        )
    except Exception as e:
        logging.error(f"trigger_logistics FAILED: {e}")
        logging.error(traceback.format_exc())
        return func.HttpResponse(
            f"Error: {e}\n\n{traceback.format_exc()}",
            status_code=500,
        )


@app.route(route="trigger/morning-sales", auth_level=func.AuthLevel.FUNCTION)
def trigger_morning_sales(req: func.HttpRequest) -> func.HttpResponse:
    """POST /api/trigger/morning-sales?code=<function-key>&mode=test-morning
    Dispara el reporte comercial. Modo default 'test-morning' (solo a Mateo).
    Modos: morning | test-morning | dry-morning
    """
    mode = (req.params.get("mode") or "test-morning").strip()
    logging.info(f"trigger_morning_sales: HTTP trigger fired, mode={mode}")
    sys.argv = ["function_app", mode]
    try:
        from daily_report import main
        result = main()
        return func.HttpResponse(
            f"Reporte comercial ({mode}) ejecutado. Exit code: {result}",
            status_code=200,
        )
    except Exception as e:
        logging.error(f"trigger_morning_sales FAILED: {e}")
        logging.error(traceback.format_exc())
        return func.HttpResponse(
            f"Error: {e}\n\n{traceback.format_exc()}",
            status_code=500,
        )
