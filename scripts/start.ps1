param([switch]$SkipInstall, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            py -3 -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ is required'"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required' }
            py -3 -m venv .venv
        } else {
            python -c "import sys; assert sys.version_info >= (3,12), 'Python 3.12+ is required'"
            if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required' }
            python -m venv .venv
        }
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required' }
    }
    & $taskPython -c "import sys; assert sys.version_info >= (3,12)"
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment is unavailable. Preserve it elsewhere, then run this script again to recreate .venv.' }
    if (-not $SkipInstall) {
        & $taskPython -m pip install -r requirements.lock
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
        & $taskPython -m pip install --no-deps -e .
        if ($LASTEXITCODE -ne 0) { throw 'Application installation failed' }
        Push-Location -LiteralPath (Join-Path $taskRoot 'frontend')
        try {
            npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed' }
            npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
        } finally { Pop-Location }
    }
    if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
        throw 'Build frontend first; omit -SkipInstall on first start.'
    }
    Write-Host "MeetFlow: http://127.0.0.1:$Port"
    & $taskPython -m uvicorn meetflow.api:app --host 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw 'Server stopped with an error' }
} finally { Pop-Location }
