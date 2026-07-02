# start-api.ps1 — FastAPI startup script for multi-agent-pipeline
# Usage: .\scripts\start-api.ps1 [-Port 8000]

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$BaseDir = $env:MULTI_AGENT_PIPELINE_BASE_DIR
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# 1. Activate virtual environment if present
$VenvDir = Join-Path $ProjectRoot ".venv"
$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
}

# 2. Ensure base directory is set
if (-not $BaseDir) {
    $BaseDir = $ProjectRoot
}
$env:MULTI_AGENT_PIPELINE_BASE_DIR = (Resolve-Path $BaseDir).Path

Write-Host "Starting Multi-Agent Pipeline API on port $Port ..."
Write-Host "Base directory: $env:MULTI_AGENT_PIPELINE_BASE_DIR"

# 3. Start uvicorn
uvicorn src.main:app --host 0.0.0.0 --port $Port --reload
