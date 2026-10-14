# Project dependencies only: no system installers, services, or credential reads.
[CmdletBinding()]
param(
    [string]$Python = 'python',
    [switch]$CheckOnly,
    [switch]$SkipPython,
    [switch]$SkipNode,
    [switch]$WithDevDependencies,
    [switch]$WithPackaging
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Command failed: {0} (exit {1})' -f $Executable, $LASTEXITCODE)
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This script requires Windows. Install project dependencies manually on other platforms.'
}
$null = Get-Command git -ErrorAction Stop
$null = Get-Command $Python -ErrorAction Stop
$null = Get-Command node -ErrorAction Stop
$null = Get-Command npm.cmd -ErrorAction Stop
$pythonInfo = & $Python -c 'import sys,json; print(json.dumps(list(sys.version_info[:3])))'
if ($LASTEXITCODE -ne 0) { throw 'Python did not start. Specify -Python with an installed Python executable.' }
$pythonVersion = ConvertFrom-Json ($pythonInfo -join '')
if ($pythonVersion[0] -ne 3 -or $pythonVersion[1] -lt 12) {
    throw 'Python 3.12 or later is required. Python 3.12 x64 is the recommended baseline; install it manually.'
}
$nodeVersion = (& node -p 'process.versions.node').Trim()
if ($LASTEXITCODE -ne 0 -or [int]($nodeVersion.Split('.')[0]) -lt 20) {
    throw 'Node.js 20 or later is required. Install Node.js x64 manually.'
}
Write-Host ('Python {0}; Node {1}; Git available' -f ($pythonVersion -join '.'), $nodeVersion)
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host 'Existing FFmpeg found (optional video fallback).'
} else {
    Write-Host 'FFmpeg not found. Videos can still upload; codec fallback requires a manual installation.'
}
if ($CheckOnly) {
    Write-Host 'Environment check complete. No files changed or dependencies installed.'
    return
}

if (-not $SkipPython) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Invoke-Checked $Python @('-m', 'venv', (Join-Path $repoRoot '.venv'))
    }
    Invoke-Checked $venvPython @('-c', 'import sys; assert sys.version_info >= (3,12), "Recreate .venv with Python 3.12+"')
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $repoRoot 'brain\requirements.txt'))
    # PDF text extraction is optional in attachments.py; install it for desktop development.
    Invoke-Checked $venvPython @('-m', 'pip', 'install', 'pypdf')
    if ($WithDevDependencies) {
        Invoke-Checked $venvPython @('-m', 'pip', 'install', '-r', (Join-Path $repoRoot 'requirements-dev.txt'), 'pytest')
    }
    if ($WithPackaging) {
        Invoke-Checked $venvPython @('-m', 'pip', 'install', 'pyinstaller')
    }
}
if (-not $SkipNode) {
    Push-Location (Join-Path $repoRoot 'client')
    try {
        Invoke-Checked 'npm.cmd' @('ci', '--no-audit', '--no-fund')
        $electronExe = Join-Path (Get-Location).Path 'node_modules\electron\dist\electron.exe'
        if (-not (Test-Path -LiteralPath $electronExe)) {
            # Install only the project's declared Electron runtime, never system software.
            Invoke-Checked 'npm.cmd' @('rebuild', 'electron')
        }
        if (-not (Test-Path -LiteralPath $electronExe)) { throw 'Electron runtime is missing; check npm download/network errors.' }
        Invoke-Checked 'node' @('-e', 'require("keytar"); require("ws")')
    } finally { Pop-Location }
}

Write-Host 'Done. Existing config and credentials were not opened or changed.'
Write-Host 'Terminal 1 (repo root, local test config first): .\.venv\Scripts\python.exe brain\server.py'
Write-Host 'Terminal 2: cd client; npm start'
Write-Host 'In client settings connect to your local Brain; do not reuse an old sidecar executable.'
Write-Host 'Skill Host has no npm dependencies. Start it manually with a new local ZX_SKILL_AUTH_TOKEN.'
Write-Host 'For isolated config, migration, and optional Skill Host startup: docs/WINDOWS_DEV_HANDOFF.md'
