@echo off
REM Wrapper para Task Scheduler - corre el reporte diario y guarda log.
REM Usa PowerShell para obtener fecha (wmic ya no esta disponible en Win11 24H2).
cd /d C:\Users\Mateo
if not exist logs mkdir logs

REM Python instalado en Windows Apps (ruta completa para evitar problemas de PATH en Task Scheduler)
set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe

REM Fecha YYYYMMDD via PowerShell (mas confiable que wmic)
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"`) do set TODAY=%%I

if "%TODAY%"=="" set TODAY=fallback
set LOG=logs\morning-%TODAY%.log

echo. >> "%LOG%"
echo === %date% %time% (run_morning.bat) === >> "%LOG%"
"%PYTHON_EXE%" daily_report.py morning >> "%LOG%" 2>&1
set PYEXIT=%errorlevel%
echo Exit code: %PYEXIT% >> "%LOG%"
exit /b %PYEXIT%
