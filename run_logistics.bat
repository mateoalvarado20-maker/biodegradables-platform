@echo off
REM Wrapper para Task Scheduler — reporte diario de logística para Gabriela.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set ldt=%%I
set LOG=logs\logistics-%ldt:~0,8%.log

echo. >> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON_EXE%" daily_logistics_report.py morning >> "%LOG%" 2>&1
echo Exit code: %errorlevel% >> "%LOG%"
