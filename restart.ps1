# restart.ps1 — One-command restart for the AlphaBot v3 daemon (app.py).
#
# Usage: .\restart.ps1
#
# What it does:
#   1. Finds the running python.exe process whose CommandLine matches "* app.py*".
#      The filter uses Name='python.exe' AND CommandLine -like '* app.py*' so it
#      never self-matches this script's own PowerShell invocation.
#   2. Stops that process if found (gracefully handles the not-running case).
#   3. Relaunches python.exe app.py in Hidden window from the project root.
#   4. Waits ~5 s, confirms the new process is up, prints its PID + CreationDate.
#   5. Optionally curls http://localhost:5000/ and reports the HTTP status.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = $PSScriptRoot

# --- 1. Find the running daemon ---
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '* app.py*' }

if ($existing) {
    Write-Host "Found daemon PID $($existing.ProcessId) — stopping..."
    Stop-Process -Id $existing.ProcessId -Force
    Write-Host "Stopped PID $($existing.ProcessId)."
} else {
    Write-Host "No running daemon found — proceeding to launch."
}

# --- 2. Relaunch ---
Write-Host "Launching daemon from: $ProjectRoot"
Start-Process python.exe `
    -ArgumentList "app.py" `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden

# --- 3. Wait and confirm ---
Start-Sleep -Seconds 5

$newProc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '* app.py*' }

if ($newProc) {
    Write-Host "Daemon is UP — PID: $($newProc.ProcessId)  Started: $($newProc.CreationDate)"
} else {
    Write-Warning "Daemon process not found after 5 s — check for startup errors."
}

# --- 4. Optional HTTP health check ---
try {
    $response = Invoke-WebRequest -Uri 'http://localhost:5000/' -UseBasicParsing -TimeoutSec 5
    Write-Host "HTTP GET /  =>  $($response.StatusCode)"
} catch {
    Write-Warning "HTTP health check failed: $_"
}
