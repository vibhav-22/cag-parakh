<#
.SYNOPSIS
    Launch the document-suspicion backend, verifying the local VLM first.

.DESCRIPTION
    Checks that the configured OpenAI-compatible VLM server (LM Studio by
    default) is reachable before starting uvicorn, so a stopped model server
    fails fast with a clear message instead of surfacing later as a broken
    "Ask document" feature.

.EXAMPLE
    ./backend/run.ps1
    ./backend/run.ps1 -Port 8001 -NoReload
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"

# Resolve config from environment, falling back to the local LM Studio defaults.
$vlmBaseUrl = if ($env:VLM_BASE_URL) { $env:VLM_BASE_URL } else { "http://127.0.0.1:1234/v1" }
$vlmModel   = if ($env:VLM_MODEL)    { $env:VLM_MODEL }    else { "qwen3-vl-4b-instruct" }
$modelsUrl  = ($vlmBaseUrl.TrimEnd("/")) + "/models"

# Run from the repository root so `backend.app:app` resolves regardless of cwd.
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "Checking VLM server at $modelsUrl ..." -ForegroundColor Cyan
try {
    $response = Invoke-RestMethod -Uri $modelsUrl -TimeoutSec 5 -Method Get
} catch {
    Write-Host "ERROR: Could not reach the VLM server at $vlmBaseUrl" -ForegroundColor Red
    Write-Host "Start LM Studio -> Developer -> Local Server, load '$vlmModel', and toggle the server to Running." -ForegroundColor Yellow
    exit 1
}

$modelIds = @($response.data | ForEach-Object { $_.id })
if ($modelIds -notcontains $vlmModel) {
    Write-Host "WARNING: Model '$vlmModel' not found on the server." -ForegroundColor Yellow
    Write-Host "Available models: $($modelIds -join ', ')" -ForegroundColor Yellow
    Write-Host "Set VLM_MODEL to one of the above, or load '$vlmModel' in LM Studio." -ForegroundColor Yellow
} else {
    Write-Host "VLM server is up and '$vlmModel' is loaded." -ForegroundColor Green
}

# Ensure the VLM feature is enabled for this run if not already configured.
if (-not $env:VLM_ENABLED) { $env:VLM_ENABLED = "1" }

$uvicornArgs = @("-m", "uvicorn", "backend.app:app", "--host", "127.0.0.1", "--port", "$Port")
if (-not $NoReload) { $uvicornArgs += "--reload" }

Write-Host "Starting backend on http://127.0.0.1:$Port ..." -ForegroundColor Cyan
& python @uvicornArgs
