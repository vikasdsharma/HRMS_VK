@echo off
rem Stops the HRMS server if running, then starts it again in a new window.
cd /d "%~dp0"
call "%~dp0stop.bat" nopause
timeout /t 2 /nobreak >nul
start "HRMS Server" "%~dp0run.bat"
echo HRMS restarting - browser will open shortly.
timeout /t 3 >nul
