@echo off
rem Stops the server if it is running, then starts it again in a new window.
cd /d "%~dp0"
call "%~dp0stop.bat" nopause
timeout /t 2 /nobreak >nul
start "HRMS Server" "%~dp0run.bat"
echo HRMS is restarting - the browser opens in a few seconds.
timeout /t 3 >nul
