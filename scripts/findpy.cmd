@echo off
rem Finds a Python that has the HRMS packages and sets %PY%.  Usage: call scripts\findpy.cmd
set "PY="
if exist "%~dp0..\.venv\Scripts\python.exe" (
  "%~dp0..\.venv\Scripts\python.exe" -c "import flask, waitress, alembic" >nul 2>&1 && set "PY=%~dp0..\.venv\Scripts\python.exe"
)
if not defined PY if exist "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" (
  "%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe" -c "import flask, waitress, alembic" >nul 2>&1 && set "PY=%LOCALAPPDATA%\Python\pythoncore-3.14-64\python.exe"
)
if not defined PY (
  py -3 -c "import flask, waitress, alembic" >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
  python -c "import flask, waitress, alembic" >nul 2>&1 && set "PY=python"
)
