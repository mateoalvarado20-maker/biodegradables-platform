@echo off
REM Wrapper para Task Scheduler - corre el reply agent y guarda log.
cd /d C:\Users\Mateo
if not exist logs mkdir logs

REM Python instalado en Windows Apps (ruta completa para evitar problemas de PATH en Task Scheduler)
set PYTHON_EXE=C:\Users\Mateo\AppData\Local\Microsoft\WindowsApps\python.exe

REM Nombre de archivo log: reply-YYYYMMDD.log (formato independiente de locale)
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set ldt=%%I
set LOG=logs\reply-%ldt:~0,8%.log

echo. >> "%LOG%"
echo === %date% %time% === >> "%LOG%"
"%PYTHON_EXE%" reply_agent.py >> "%LOG%" 2>&1
echo Exit code: %errorlevel% >> "%LOG%"
