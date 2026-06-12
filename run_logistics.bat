@echo off
REM Wrapper para Task Scheduler — reporte diario de logística para Gabriela.
REM Usa PowerShell para obtener fecha (wmic ya no esta disponible en Win11 24H2).
REM NOTA: la schtask local esta DESHABILITADA; en produccion corre el timer
REM `logistics_morning` de Azure Functions. Este wrapper queda para runs manuales.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe
set PYTHONIOENCODING=utf-8

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"`) do set TODAY=%%I
if "%TODAY%"=="" set TODAY=fallback
set LOG=logs\logistics-%TODAY%.log

echo. >> "%LOG%"
echo === %date% %time% (run_logistics.bat) === >> "%LOG%"
"%PYTHON_EXE%" daily_logistics_report.py morning >> "%LOG%" 2>&1
set PYEXIT=%errorlevel%
echo Exit code: %PYEXIT% >> "%LOG%"
exit /b %PYEXIT%
