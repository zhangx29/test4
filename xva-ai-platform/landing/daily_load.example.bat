@echo off
rem Daily XVA landing. Copy to daily_load.bat, edit the paths, and schedule it (Task Scheduler) after the
rem official XVA run. Usage: daily_load.bat 2026-11-20
setlocal
set ASOF=%1
if "%ASOF%"=="" (
  echo Usage: daily_load.bat YYYY-MM-DD
  exit /b 2
)
set KIT=C:\BMOdev\xva-copilot-starter\xva-ai-platform
set ROOT=C:\xva_data
set STORE=%ROOT%\store\xva_store.duckdb
set INBOX=%ROOT%\inbox\%ASOF%
set CFG=%KIT%\landing\landing_config.yaml

rem 1. Official XVA run: values (required); delete --sens or --pnl if you don't produce those files
python -m xva_landing.land_extract --config "%CFG%" --store "%STORE%" --archive "%ROOT%\archive" ^
  xva --as-of %ASOF% --values "%INBOX%\xva_values.csv" --sens "%INBOX%\sensitivities.csv" --pnl "%INBOX%\pnl_explain.csv"
if errorlevel 1 (
  echo XVA load for %ASOF% was rejected or failed - see the messages above.
  exit /b 1
)

rem 2. MFL workbook check (remove if you don't land the MFL workbook; then also remove mfl_spreadsheet
rem    from expected_sources in dictionary.yaml)
if exist "%INBOX%\mfl.xlsx" python -m xva_landing.land_mfl_workbook --file "%INBOX%\mfl.xlsx" --as-of %ASOF% --store "%STORE%"

rem 3. Show the day's status
set XVA_ENV=dev
python -m xva_mcp.cli --store "%STORE%" status --as-of %ASOF%
