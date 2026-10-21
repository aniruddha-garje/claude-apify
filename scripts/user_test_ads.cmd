@echo off
rem Test the public ads Actor as a user from your second Apify account. Sign in with the TEST account when the browser opens.
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
call apify login
".venv\Scripts\python.exe" scripts\user_test.py ads
echo.
echo Done. Now sign back in with your MAIN account:
call apify login
