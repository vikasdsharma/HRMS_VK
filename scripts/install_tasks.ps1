<#
  Installs two Windows scheduled tasks so HRMS runs unattended:
    HRMS Server       starts at boot, restarts itself if it crashes
    HRMS Maintenance  daily 02:00 - scheduled database backup + retention purge
  Run once from an ADMINISTRATOR PowerShell:   powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1
  Remove with:                                 powershell -ExecutionPolicy Bypass -File scripts\install_tasks.ps1 -Remove
#>
param([switch]$Remove)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    foreach ($n in "HRMS Server", "HRMS Maintenance") {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $n -Confirm:$false; "Removed $n" }
    }
    return
}

# server: a tiny launcher so the scheduler runs the same thing run.bat does (without opening a browser)
$launcher = Join-Path $root "scripts\server_task.cmd"
@"
@echo off
cd /d "$root"
call scripts\findpy.cmd
if not defined PY exit /b 1
%PY% serve.py >> data\logs\server_stdout.log 2>&1
"@ | Set-Content -Path $launcher -Encoding ASCII

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName "HRMS Server" -Force -Settings $settings -Principal $principal `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Action (New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$launcher`"" -WorkingDirectory $root) | Out-Null

Register-ScheduledTask -TaskName "HRMS Maintenance" -Force -Principal $principal `
    -Trigger (New-ScheduledTaskTrigger -Daily -At 2:00am) `
    -Action (New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$root\scripts\maintenance.cmd`"" -WorkingDirectory $root) | Out-Null

"Installed. Start the server now with:  Start-ScheduledTask -TaskName 'HRMS Server'"
"Backups are written to  $root\data\backups  (copy them off this machine regularly!)"
