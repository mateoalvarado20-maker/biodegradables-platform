# Archivo de código retirado

Nada en esta carpeta corre en producción. Se preserva por referencia histórica.
**No restaurar nada de aquí sin leer la justificación y el informe de auditoría
(`AUDITORIA_TECNICA_2026-06-12.md`).**

| Archivo | Retirado | Por qué |
|---|---|---|
| `weekly_report.py` + `run_weekly_report.bat` | 2026-06-12 (Fase 0) | Huérfano (la schtask nunca se creó) y ROTO: llama `activity_state.get_week(wk)` con la firma vieja — hoy interpretaría la semana como email. Funcionalmente reemplazado por el job `weekly_summaries` del bot (teams_bot.py, viernes 17:00 EC), que incluye a Mateo vía `TRACKER_EMAIL_TO_MATEO`. |
| `agent.py` | 2026-06-12 (Fase 0) | REPL interactivo contra Power BI Desktop local. Nadie lo importa; superado por `ask_agent.py`. Su loop de tools era `while True` sin límite. |
| `apollo_orchestrator.py` / `.json` / `run_apollo_orchestrator.bat` | 2026-06-12 (Fase 0) | Orquestador "1 secuencia activa" descartado por el usuario el 2026-05-28 (limitaba volumen). Ya estaba doblemente deshabilitado (schtask eliminada + timer Azure con app setting). Se preserva por si la lógica se reaprovecha. |
| `run_reply_agent.bat` | 2026-06-12 (Fase 0) | La schtask local está deshabilitada y el reply agent corre en Azure Functions (`reply_agent_tick`). Re-habilitar la tarea local causaría BORRADORES DUPLICADOS a prospectos (states disjuntos local vs Azure Table — hallazgo P3 de la auditoría). Se archiva el wrapper para impedir el re-enable accidental. Para un run manual local: `python reply_agent.py --dry-run`. |
| `bot_deploy_stage_2026-06-02/` (solo en disco, no en git) | 2026-06-12 (Fase 0) | Staging del deploy del bot del 2/6 — copia VIEJA del código (otra fuente de drift). El artefacto de rollback real es `bot_deploy.zip`. Desde Fase 4, los paquetes de deploy se generan con `tools/`. |
