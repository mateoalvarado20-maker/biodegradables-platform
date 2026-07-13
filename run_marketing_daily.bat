@echo off
REM Corrida diaria del departamento de Marketing (M1) — Task Scheduler.
REM Logs: logs\marketing-AAAAMMDD.log · Estado: python -m marketing.daily_run status
REM ACTIVAR SOLO tras el merge a master (ROADMAP M1): la tarea se crea con
REM   schtasks /create /tn "VERIA-Marketing-Daily" /tr "C:\Users\Mateo\run_marketing_daily.bat" /sc daily /st 07:30 /ru "Mateo" /rl LIMITED /f
cd /d C:\Users\Mateo
set PATH=%PATH%;C:\Users\Mateo\tools\node-v22.14.0-win-x64
for /f "tokens=1-3 delims=/-" %%a in ("%date%") do set FECHA=%%c%%b%%a
if not exist logs mkdir logs
python -X utf8 -m marketing.daily_run run >> logs\marketing-%FECHA%.log 2>&1
