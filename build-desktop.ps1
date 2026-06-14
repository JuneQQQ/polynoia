# build-desktop.ps1 - Build the Windows desktop app (Tauri 2 -> setup.exe).
# Produces an NSIS installer (and an .msi) under
#   apps\desktop\src-tauri\target\release\bundle\
# Usage:  powershell -ExecutionPolicy Bypass -File .\build-desktop.ps1
#
# NOTE: messages are ASCII-only on purpose. Windows PowerShell 5.1 parses a
# BOM-less UTF-8 .ps1 as the system ANSI codepage, which corrupts non-ASCII
# (e.g. CJK) string literals and breaks the script. Keep this file ASCII.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# Make sure cargo is visible even in a shell opened before rustup was installed.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) { $env:Path = "$cargoBin;$env:Path" }

function Have($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

Write-Host "==> Preflight: build toolchain" -ForegroundColor Cyan

# --- Rust (Tauri compiles a native Rust binary) ---
if (-not (Have cargo)) {
    Write-Host "[X] Rust toolchain not found (no cargo)." -ForegroundColor Red
    Write-Host "    Tauri must compile with Rust. Install it:" -ForegroundColor Yellow
    Write-Host "    1) MSVC C++ build tools (VS Build Tools, 'Desktop development with C++')" -ForegroundColor Yellow
    Write-Host "       https://visualstudio.microsoft.com/visual-cpp-build-tools/" -ForegroundColor Yellow
    Write-Host "    2) Rust:  winget install Rustlang.Rustup   (or https://rustup.rs)" -ForegroundColor Yellow
    Write-Host "    Then open a NEW terminal and re-run this script." -ForegroundColor Yellow
    throw "missing Rust toolchain"
}
Write-Host ("cargo: " + (cargo --version)) -ForegroundColor Green

# --- uv (the packaged app runs the Python backend via `uv run` at runtime) ---
if (-not (Have uv)) {
    Write-Host "[!] uv not on PATH. The packaged app needs uv to launch its embedded" -ForegroundColor Yellow
    Write-Host "    backend; the end user's machine needs uv too. Run .\install.ps1 or" -ForegroundColor Yellow
    Write-Host "    see https://astral.sh/uv to install." -ForegroundColor Yellow
} else {
    Write-Host ("uv:    " + (uv --version)) -ForegroundColor Green
}

# --- Tauri CLI (devDependency of apps/desktop) ---
if (-not (Test-Path (Join-Path $root "apps\desktop\node_modules\.bin\tauri.cmd")) -and
    -not (Test-Path (Join-Path $root "apps\desktop\node_modules\.bin\tauri"))) {
    Write-Host "==> Installing JS deps (pnpm install)" -ForegroundColor Cyan
    Push-Location $root
    try { pnpm install } finally { Pop-Location }
}

# --- Stage a bundled uv.exe so target machines need nothing on PATH ---
# The Rust launcher prefers <resources>\bin\uv.exe over a PATH uv. Copy the local
# uv here at build time (kept out of git via .gitignore).
$binDir = Join-Path $root "apps\desktop\src-tauri\resources\bin"
New-Item -ItemType Directory -Force -Path $binDir | Out-Null
$uvSrc = (Get-Command uv -ErrorAction SilentlyContinue).Source
if ($uvSrc -and (Test-Path $uvSrc)) {
    Copy-Item $uvSrc (Join-Path $binDir "uv.exe") -Force
    Write-Host ("==> Bundled uv: " + (Join-Path $binDir "uv.exe")) -ForegroundColor Cyan
} else {
    Write-Host "[!] uv not found to bundle; the app will need uv on the target PATH." -ForegroundColor Yellow
}

# --- Build (beforeBuildCommand builds web dist + stages the server resource) ---
# --bundles nsis: produce ONLY the setup.exe. tauri.conf.json keeps targets="all"
# (so mac/linux still get their native bundles); we override here to skip the MSI
# target, whose WiX toolchain download from GitHub is flaky behind some networks.
Write-Host "`n==> Building desktop bundle (compiles Rust - first run is slow)" -ForegroundColor Cyan
Push-Location (Join-Path $root "apps\desktop")
try { pnpm tauri build --bundles nsis } finally { Pop-Location }

# --- Surface the artifacts ---
$bundle = Join-Path $root "apps\desktop\src-tauri\target\release\bundle"
Write-Host "`nDone. Installers:" -ForegroundColor Green
if (Test-Path $bundle) {
    Get-ChildItem -Path $bundle -Recurse -Include *.exe, *.msi -ErrorAction SilentlyContinue |
        ForEach-Object { Write-Host ("  " + $_.FullName) -ForegroundColor Green }
} else {
    Write-Host "  (no bundle dir found - check the build log above)" -ForegroundColor Yellow
}
