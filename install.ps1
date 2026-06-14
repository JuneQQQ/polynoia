# install.ps1 — Windows equivalent of `make install`
# Installs backend (uv) + frontend (pnpm) dependencies.
# Usage:  powershell -ExecutionPolicy Bypass -File .\install.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

Write-Host "==> Checking toolchain" -ForegroundColor Cyan

# --- uv (Python package manager) ---
if (-not (Have uv)) {
    Write-Host "uv not found, installing via the official installer..." -ForegroundColor Yellow
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # The installer adds uv to a new shell's PATH; surface it for this session too.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Have uv)) {
        throw "uv install finished but 'uv' is still not on PATH. Open a NEW terminal and re-run .\install.ps1"
    }
}
Write-Host ("uv:   " + (uv --version)) -ForegroundColor Green

# --- pnpm (canonical JS package manager) ---
if (-not (Have pnpm)) {
    Write-Host "pnpm not found, installing globally via npm..." -ForegroundColor Yellow
    npm install -g pnpm
    if (-not (Have pnpm)) {
        throw "pnpm install finished but 'pnpm' is still not on PATH. Open a NEW terminal and re-run .\install.ps1"
    }
}
Write-Host ("pnpm: " + (pnpm --version)) -ForegroundColor Green

# --- Backend deps (--extra dev pulls pytest/ruff/mypy so tests work) ---
Write-Host "`n==> Installing backend deps (uv sync --extra dev)" -ForegroundColor Cyan
Push-Location (Join-Path $root "apps\server")
try { uv sync --extra dev } finally { Pop-Location }

# --- Frontend deps (workspace install from repo root) ---
Write-Host "`n==> Installing frontend deps (pnpm install)" -ForegroundColor Cyan
Push-Location $root
try { pnpm install } finally { Pop-Location }

Write-Host "`nDone. Run the app with:  .\dev.ps1" -ForegroundColor Green
