rem Daily maintenance: scheduled backup + retention purge.  Used by the scheduled task from scripts\install_tasks.ps1
@echo off
cd /d "%~dp0.."
call scripts\findpy.cmd
%PY% manage.py maintenance >> data\logs\maintenance.log 2>&1
