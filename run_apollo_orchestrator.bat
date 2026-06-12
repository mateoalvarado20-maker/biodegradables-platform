@echo off
REM Wrapper para Task Scheduler - corre el orquestador Apollo y guarda log.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

REM Python instalado en Windows Apps (ruta completa para evitar problemas de PATH en Task Scheduler)
set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe

REM Forzar UTF-8 en stdout para que las flechas Unicode no rompan el log
set PYTHONIOENCODING=utf-8

REM Nombre de archivo log: apollo-YYYYMMDD.log
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set ldt=%%I
set LOG=logs\apollo-%ldt:~0,8%.log

echo. >> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON_EXE%" apollo_orchestrator.py --verbose >> "%LOG%" 2>&1
echo Exit code: %errorlevel% >> "%LOG%"
