# restart.ps1 - One-command restart for the AlphaBot v3 daemon (app.py).
#
# Usage: .\restart.ps1
#
# What it does:
#   1. Finds running python.exe processes whose CommandLine matches "* app.py*".
#      The filter uses Name='python.exe' AND CommandLine -like '* app.py*' so it
#      never self-matches this script's own PowerShell invocation, and never
#      matches alpha_bot_execution.py subprocesses (their CommandLine contains
#      'alpha_bot_execution.py', not 'app.py' at the daemon-launch position).
#   2. Stops all matching processes (handles zero, one, or many -- e.g. if a
#      previous restart left a stale daemon alongside a newly-spawned one).
#   3. Relaunches python.exe app.py in Hidden window from the project root.
#   4. Waits ~5 s, confirms the new process is up, prints its PID + CreationDate.
#   5. Optionally curls http://localhost:5000/ and reports the HTTP status.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot

# --- 1. Find the running daemon(s) ---
# Coerce to array so .Count and foreach work correctly whether 0, 1, or N procs match.
$existing = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '* app.py*' }
)

if ($existing.Count -gt 0) {
    Write-Host "Found $($existing.Count) daemon process(es) -- stopping..."
    foreach ($proc in $existing) {
        Write-Host "  Stopping PID $($proc.ProcessId)..."
        Stop-Process -Id $proc.ProcessId -Force
        Write-Host "  Stopped PID $($proc.ProcessId)."
    }
} else {
    Write-Host "No running daemon found -- proceeding to launch."
}

# --- 2. Relaunch ---
Write-Host "Launching daemon from: $ProjectRoot"
Start-Process python.exe `
    -ArgumentList "app.py" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden

# --- 3. Wait and confirm ---
Start-Sleep -Seconds 5

# Coerce to array for the same reason as above.
$newProcs = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '* app.py*' }
)

if ($newProcs.Count -gt 0) {
    foreach ($proc in $newProcs) {
        Write-Host "Daemon is UP -- PID: $($proc.ProcessId)  Started: $($proc.CreationDate)"
    }
} else {
    Write-Warning "Daemon process not found after 5 s -- check for startup errors."
}

# --- 4. Optional HTTP health check ---
try {
    $response = Invoke-WebRequest -Uri 'http://localhost:5000/' -UseBasicParsing -TimeoutSec 5
    Write-Host "HTTP GET /  =>  $($response.StatusCode)"
} catch {
    Write-Warning "HTTP health check failed: $_"
}
