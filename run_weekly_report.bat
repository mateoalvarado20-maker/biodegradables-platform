@echo off
REM Wrapper para Task Scheduler -- corre el reporte semanal viernes 5 PM y guarda log.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

REM Python instalado en Windows Apps (ruta completa para evitar problemas de PATH)
set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe

REM Nombre de archivo log: weekly-YYYYMMDD.log (formato independiente de locale)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set ldt=%%I
set LOG=logs\weekly-%ldt:~0,8%.log

echo. >> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON_EXE%" weekly_report.py send >> "%LOG%" 2>&1
echo Exit code: %errorlevel% >> "%LOG%"
