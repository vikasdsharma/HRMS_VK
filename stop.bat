@echo off
rem Stops the HRMS server (whatever is listening on its port; default 5000).
setlocal
if not defined HRMS_PORT set HRMS_PORT=5000
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %HRMS_PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; Write-Host \"HRMS stopped (port %HRMS_PORT%).\" } else { Write-Host \"HRMS is not running on port %HRMS_PORT%.\" }"
if /i not "%1"=="nopause" pause
