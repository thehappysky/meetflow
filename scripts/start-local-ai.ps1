param([switch]$SkipInstall, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
$taskRuntime = Join-Path $taskRoot 'data\tools\ollama-v0.40.16\ollama.exe'
$taskTokenizer = Join-Path $taskRoot 'data\models\tokenizers\qwen35-4b\851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a\tokenizer.json'
$taskKeys = @('OLLAMA_HOST', 'OLLAMA_MODELS', 'OLLAMA_MAX_LOADED_MODELS', 'OLLAMA_NUM_PARALLEL',
    'MEETFLOW_MODEL_NAME', 'MEETFLOW_MODEL_BASE_URL', 'MEETFLOW_MODEL_API_PROFILE',
    'MEETFLOW_MODEL_TOKENIZER_PATH', 'MEETFLOW_MODEL_CONTEXT_WINDOW', 'MEETFLOW_MODEL_TIMEOUT',
    'MEETFLOW_MODEL_TEMPERATURE', 'MEETFLOW_MODEL_SEED',
    'MEETFLOW_MEETING_WORKFLOW', 'MEETFLOW_EVIDENCE_REFERENCE_PROTOCOL', 'MEETFLOW_EVIDENCE_PROMPT_REVISION',
    'MEETFLOW_EVIDENCE_COVERAGE', 'MEETFLOW_ASR_DEVICE',
    'MEETFLOW_MAX_AGENT_STEPS', 'MEETFLOW_MAX_OUTPUT_TOKENS', 'MEETFLOW_RUN_TIMEOUT')
$taskOld = @{}
foreach ($key in $taskKeys) { $taskOld[$key] = [Environment]::GetEnvironmentVariable($key, 'Process') }
Push-Location -LiteralPath $taskRoot
try {
    foreach ($path in @($taskPython, $taskRuntime, $taskTokenizer)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing prepared local AI dependency: $path. See docs/35-backend-runtime-acceptance.md." }
    }
    & $taskPython -c "import tokenizers,faster_whisper; from pathlib import Path; from meetflow.config import Settings; assert Path(Settings().asr_model).is_dir(), 'Configure an existing local ASR directory first'"
    if ($LASTEXITCODE -ne 0) { throw 'Local AI dependencies or ASR model are not ready; see docs/35-backend-runtime-acceptance.md.' }
    try { $taskServer = Invoke-RestMethod 'http://127.0.0.1:11435/api/version' -TimeoutSec 3 }
    catch { $taskServer = $null }
    if (-not $taskServer) {
        if (Get-NetTCPConnection -State Listen -LocalPort 11435 -ErrorAction SilentlyContinue) {
            throw 'Port 11435 is occupied by another service; it has not been stopped.'
        }
        $env:OLLAMA_HOST = '127.0.0.1:11435'
        $env:OLLAMA_MODELS = Join-Path $taskRoot 'data\models\ollama-modern'
        $env:OLLAMA_MAX_LOADED_MODELS = '1'
        $env:OLLAMA_NUM_PARALLEL = '1'
        $taskLogs = Join-Path $taskRoot 'data\logs'
        New-Item -ItemType Directory -Path $taskLogs -Force | Out-Null
        $taskStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        Start-Process -FilePath $taskRuntime -ArgumentList 'serve' -WindowStyle Hidden -WorkingDirectory $taskRoot `
            -RedirectStandardOutput (Join-Path $taskLogs "ollama-$taskStamp.out.log") `
            -RedirectStandardError (Join-Path $taskLogs "ollama-$taskStamp.err.log") | Out-Null
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            try { $taskServer = Invoke-RestMethod 'http://127.0.0.1:11435/api/version' -TimeoutSec 2; break }
            catch { Start-Sleep -Milliseconds 500 }
        }
    }
    if (-not $taskServer -or $taskServer.version -ne '0.40.16') { throw 'Expected the verified portable Ollama 0.40.16 on port 11435.' }
    $taskModels = Invoke-RestMethod 'http://127.0.0.1:11435/api/tags' -TimeoutSec 5
    if ('qwen3.5:4b-q4_K_M' -notin $taskModels.models.name) { throw 'Prepare the pinned qwen3.5:4b-q4_K_M model first; no automatic model download.' }
    $env:MEETFLOW_MODEL_NAME = 'qwen3.5:4b-q4_K_M'
    $env:MEETFLOW_MODEL_BASE_URL = 'http://127.0.0.1:11435'
    $env:MEETFLOW_MODEL_API_PROFILE = 'ollama_json'
    $env:MEETFLOW_MODEL_TOKENIZER_PATH = $taskTokenizer
    $env:MEETFLOW_MODEL_CONTEXT_WINDOW = '8192'
    $env:MEETFLOW_MODEL_TIMEOUT = '180'
    $env:MEETFLOW_MODEL_TEMPERATURE = '0'
    $env:MEETFLOW_MODEL_SEED = '2026'
    $env:MEETFLOW_MEETING_WORKFLOW = 'evidence_v1'
    $env:MEETFLOW_EVIDENCE_REFERENCE_PROTOCOL = 'refs_v1'
    $env:MEETFLOW_EVIDENCE_PROMPT_REVISION = 'state_v2'
    $env:MEETFLOW_EVIDENCE_COVERAGE = 'true'
    $env:MEETFLOW_ASR_DEVICE = 'cpu'
    $env:MEETFLOW_MAX_AGENT_STEPS = '6'
    $env:MEETFLOW_MAX_OUTPUT_TOKENS = '2048'
    $env:MEETFLOW_RUN_TIMEOUT = '900'
    Write-Host 'Real local ASR and multi-agent analysis enabled. Output is an AI draft requiring review; quality limits are documented.'
    & (Join-Path $PSScriptRoot 'start.ps1') -SkipInstall:$SkipInstall -Port $Port
} finally {
    foreach ($key in $taskKeys) { [Environment]::SetEnvironmentVariable($key, $taskOld[$key], 'Process') }
    Pop-Location
}
