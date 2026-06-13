# schedule_prism.ps1 — Register the Market Prism nightly scheduler as a Windows Task.
#
# Usage:
#   Run once as Administrator (or current user with Task Scheduler access):
#     powershell -ExecutionPolicy Bypass -File schedule_prism.ps1
#
# To unregister (teardown):
#   Unregister-ScheduledTask -TaskName "PlanetStopperMarketPrism" -Confirm:$false
#
# See feature-plans/market-prism-phase4-unattended-scheduling.md for design rationale.

$TaskName    = "PlanetStopperMarketPrism"
$ProjectRoot = "C:\Users\paulm\Documents\Projects\POC\AlphaBotPM"
$Python      = (Get-Command python).Source
$Script      = Join-Path $ProjectRoot "prism_scheduler.py"

# Trigger: daily at 03:00 local time (US Central on this host)
$Trigger = New-ScheduledTaskTrigger -Daily -At "03:00AM"

# Action: python prism_scheduler.py, working directory = project root
$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument $Script `
    -WorkingDirectory $ProjectRoot

# Settings: run whether logged on or not; start when available if missed
$Settings = New-ScheduledTaskSettingsSet `
    -RunOnlyIfNetworkAvailable `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

# Principal: run as current user (adjust to a service account for unattended server deploy)
$Principal = New-ScheduledTaskPrincipal `
    -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType S4U `
    -RunLevel Limited

# Description for Task Scheduler UI
$Task = New-ScheduledTask `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Market Prism nightly run — Phase 4 Option B (Windows Task Scheduler). Invokes prism_scheduler.py which runs the prism-synthesizer Claude agent. Idempotency guard prevents double-writes. See feature-plans/market-prism-phase4-unattended-scheduling.md."

# Register (or update if task already exists)
Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force

Write-Output "Task '$TaskName' registered. Runs daily at 03:00 local time from $ProjectRoot."
Write-Output ""
Write-Output "To unregister:  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Output "To run now:     Start-ScheduledTask -TaskName '$TaskName'"
Write-Output "To check last:  Get-ScheduledTaskInfo -TaskName '$TaskName'"
