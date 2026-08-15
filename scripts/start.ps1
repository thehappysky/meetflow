param([switch]$SkipInstall, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $taskRoot
try {
    $taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required' }
    }
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
