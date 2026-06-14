# dev.ps1 — Windows equivalent of `make dev`
# Starts the FastAPI backend and the Vite frontend in parallel.
# Usage:
#   .\dev.ps1            # two separate windows (default, robust)
#   .\dev.ps1 -Same      # one window, interleaved logs, Ctrl-C stops both
param([switch]$Same)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Launch via `python -m polynoia`, NOT the uvicorn CLI: on Windows the launcher
# forces a subprocess-capable ProactorEventLoop so adapter CLIs + the onboarding
# probe can spawn under --reload (the uvicorn CLI gives no hook for that). Host /
# port / reload are read from env by polynoia/__main__.py.
$serverCmd = "Set-Location '$root\apps\server'; " +
             "`$env:POLYNOIA_HOST='0.0.0.0'; `$env:POLYNOIA_PORT='7780'; " +
             "uv run python -m polynoia"
$webCmd    = "Set-Location '$root'; pnpm --filter @polynoia/web dev"

if ($Same) {
    # Single console: run both as background jobs, stream their output, and
    # tear both down on Ctrl-C.
    Write-Host "==> Starting server + web (Ctrl-C stops both)" -ForegroundColor Cyan
    $jobs = @(
        Start-Job -Name polynoia-server -ScriptBlock { param($c) powershell -NoProfile -Command $c } -ArgumentList $serverCmd
        Start-Job -Name polynoia-web    -ScriptBlock { param($c) powershell -NoProfile -Command $c } -ArgumentList $webCmd
    )
    try {
        while ($true) { $jobs | Receive-Job; Start-Sleep -Milliseconds 400 }
    } finally {
        Write-Host "`n==> Stopping..." -ForegroundColor Yellow
        $jobs | Stop-Job -ErrorAction SilentlyContinue
        $jobs | Remove-Job -Force -ErrorAction SilentlyContinue
    }
} else {
    # Two windows: each service gets its own console with live, readable logs.
    Write-Host "==> Launching backend  -> http://localhost:7780" -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $serverCmd
    Write-Host "==> Launching frontend -> Vite dev server (see its window for the URL)" -ForegroundColor Cyan
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCmd
    Write-Host "`nTwo windows opened. Close them (or Ctrl-C inside each) to stop." -ForegroundColor Green
}
