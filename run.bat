@echo off
rem Starts the HRMS server and opens the browser.  Settings come from environment variables / .env (see .env.example).
setlocal
cd /d "%~dp0"
call scripts\findpy.cmd
if not defined PY (
  echo Python with the HRMS packages was not found. Run setup.bat first.
  pause
  exit /b 1
)
if not defined HRMS_PORT set HRMS_PORT=5000
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%HRMS_PORT%"
%PY% serve.py
echo.
echo The server stopped. Read the messages above if this was not expected.
pause
