@echo off
rem Stops the HRMS server (whatever is listening on port 5000).
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }; Write-Host 'HRMS stopped.' } else { Write-Host 'HRMS is not running.' }"
if /i not "%1"=="nopause" pause
