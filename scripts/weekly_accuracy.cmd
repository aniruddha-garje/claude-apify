@echo off
rem Weekly accuracy check for all three Actors. Writes reports\weekly\<date>.md and a log next to it.
cd /d "%~dp0.."
if not exist reports\weekly mkdir reports\weekly
set PYTHONIOENCODING=utf-8
set APIFY_CLI_DISABLE_TELEMETRY=1
".venv\Scripts\python.exe" scripts\accuracy_check.py >> "reports\weekly\weekly.log" 2>&1
