@echo off
cd /d "%~dp0"
set PY=C:\Users\VIKAS\AppData\Local\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python
start "" http://127.0.0.1:5000
"%PY%" app.py
pause
