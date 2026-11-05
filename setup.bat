@echo off
rem One-time (or after updates) setup: creates .venv, installs packages, upgrades the database.
setlocal
cd /d "%~dp0"
set "BASE="
if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" set "BASE=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
if not defined BASE ( py -3 --version >nul 2>&1 && set "BASE=py -3" )
if not defined BASE ( python --version >nul 2>&1 && set "BASE=python" )
if not defined BASE ( echo Install Python 3.12 or newer from python.org first. & pause & exit /b 1 )
if not exist ".venv\Scripts\python.exe" %BASE% -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 ( echo Package install failed. & pause & exit /b 1 )
.venv\Scripts\python.exe manage.py migrate
echo.
echo Setup finished. Start the application with run.bat
pause
