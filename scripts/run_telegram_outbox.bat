@echo off
setlocal EnableExtensions
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
cd /d "%PROJECT_DIR%"
if not exist "%PROJECT_DIR%\logs" mkdir "%PROJECT_DIR%\logs"
set "LOG=%PROJECT_DIR%\logs\telegram-outbox.log"
if exist "%LOG%" for %%A in ("%LOG%") do if %%~zA GTR 5242880 (
    if exist "%LOG%.1" del /q "%LOG%.1" >nul 2>&1
    move /Y "%LOG%" "%LOG%.1" >nul 2>&1
)
>>"%LOG%" echo [%date% %time%] START worker=telegram_outbox
".venv\Scripts\python.exe" manage.py telegram_outbox >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
>>"%LOG%" echo [%date% %time%] END worker=telegram_outbox exit=%RC%
exit /b %RC%
