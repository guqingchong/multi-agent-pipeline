# start-api.ps1 — FastAPI startup script for multi-agent-pipeline
# Usage: .\scripts\start-api.ps1 [-Port 8000]

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BaseDir = $env:MULTI_AGENT_PIPELINE_BASE_DIR
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Source local environment overrides (copy env.example.ps1 to env.ps1)
. "$PSScriptRoot\env.ps1"

# Sensible defaults for anything the user did not configure in env.ps1
if (-not $env:AGENT_MOCK) {
    $env:AGENT_MOCK = "true"
}
if (-not $env:MULTI_AGENT_PIPELINE_BASE_DIR) {
    $env:MULTI_AGENT_PIPELINE_BASE_DIR = "$root\projects"
}

# Allow -BaseDir parameter to override the env default
if ($BaseDir) {
    $env:MULTI_AGENT_PIPELINE_BASE_DIR = (Resolve-Path $BaseDir).Path
}
$env:PYTHONPATH = "$root\src"

# 1. Activate virtual environment if present
$VenvDir = Join-Path $root ".venv"
$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
}

Write-Host "Starting Multi-Agent Pipeline API on port $Port ..."
Write-Host "Base directory: $env:MULTI_AGENT_PIPELINE_BASE_DIR"

# 2. Start uvicorn bound to localhost only
uvicorn src.main:app --host 127.0.0.1 --port $Port --reload
