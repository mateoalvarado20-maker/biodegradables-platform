@echo off
REM Wrapper para Task Scheduler - corre el notificador Apollo y guarda log.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe
set PYTHONIOENCODING=utf-8

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set ldt=%%I
set LOG=logs\apollo-notifier-%ldt:~0,8%.log

echo. >> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON_EXE%" apollo_completion_notifier.py --verbose >> "%LOG%" 2>&1
echo Exit code: %errorlevel% >> "%LOG%"
