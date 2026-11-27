@echo off
rem Daily load of the desk's XVA CSV files into the curated store.
rem Copy to daily_load_desk.bat, check the paths below, and run it after the day's files are in the folder.
rem   daily_load_desk.bat              loads every date in the folder that is not loaded yet
rem   daily_load_desk.bat 2026-11-24   loads (or reloads) one date
rem   daily_load_desk.bat t-1          loads (or reloads) the previous TOR business day
rem Datasets set to "source: sp" in landing\desk_csv.yaml (P&L explain by default) run their stored procedures
rem through MFL, so PYTHON must be the Python where "import pymfl_setup" works (the one the LITES notebook uses).
setlocal
set PYTHON=python
set KIT=C:\BMOdev\xva-copilot-starter\xva-ai-platform
set DATA=%USERPROFILE%\xva_data
set STORE=%DATA%\store\xva_store.duckdb
set XVA_DICTIONARY_PATH=%KIT%\landing\desk_dictionary.yaml
cd /d "%KIT%"

if "%~1"=="" (
  "%PYTHON%" -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store "%STORE%" --archive "%DATA%\archive" --all
) else (
  "%PYTHON%" -m xva_landing.load_desk_csv --config landing\desk_csv.yaml --store "%STORE%" --archive "%DATA%\archive" --as-of %1
)
set RC=%ERRORLEVEL%
if not "%RC%"=="0" echo One or more dates were rejected or the setup failed - read the messages above.

set XVA_ENV=dev
"%PYTHON%" -m xva_mcp.cli --store "%STORE%" status
exit /b %RC%
