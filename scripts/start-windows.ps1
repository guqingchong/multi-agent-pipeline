# start-windows.ps1 — Windows local startup for multi-agent-pipeline
# Usage: .\scripts\start-windows.ps1

[CmdletBinding()]
param(
    [string]$BaseDir = $env:MULTI_AGENT_PIPELINE_BASE_DIR,
    [string]$Mode = "greenfield"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

# 1. Locate or create virtual environment
$VenvDir = Join-Path $ProjectRoot ".venv"
if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating virtual environment at $VenvDir ..."
    python -m venv $VenvDir
}

# 2. Activate venv
$Activate = Join-Path $VenvDir "Scripts\Activate.ps1"
if (Test-Path $Activate) {
    . $Activate
} else {
    throw "Virtual environment activation script not found: $Activate"
}

# 3. Upgrade pip and install dependencies
Write-Host "Installing dependencies ..."
python -m pip install --upgrade pip | Out-Null
python -m pip install -r (Join-Path $ProjectRoot "requirements.txt") | Out-Null

# 4. Environment defaults
if (-not $BaseDir) {
    $BaseDir = $ProjectRoot
}
$env:MULTI_AGENT_PIPELINE_BASE_DIR = (Resolve-Path $BaseDir).Path
$env:PIPELINE__PIPELINE_MODE = $Mode
if (-not $env:AGENT_MOCK) {
    $env:AGENT_MOCK = "true"
}

Write-Host "MULTI_AGENT_PIPELINE_BASE_DIR = $env:MULTI_AGENT_PIPELINE_BASE_DIR"
Write-Host "PIPELINE__PIPELINE_MODE       = $env:PIPELINE__PIPELINE_MODE"
Write-Host "AGENT_MOCK                    = $env:AGENT_MOCK"

# 5. Registry readiness check
$RegistryCheck = python -c "from src.registry import REGISTRY; print('phases:', len(REGISTRY.list_phases()), 'agents:', len(REGISTRY.list_agents()))"
if ($LASTEXITCODE -ne 0) {
    throw "Registry readiness check failed."
}
Write-Host "Registry ready: $RegistryCheck"

# 6. Show unified CLI help
Write-Host ""
Write-Host "=== pipeline.py help ==="
python (Join-Path $ProjectRoot "src\pipeline.py") --help
Write-Host ""
Write-Host "=== bridge_cli.py help ==="
python (Join-Path $ProjectRoot "src\bridge_cli.py") --help

Write-Host ""
Write-Host "Start complete. Run the API with .\scripts\start-api.ps1"
