# start-windows.ps1 — Windows local startup for multi-agent-pipeline
# Usage: .\scripts\start-windows.ps1

[CmdletBinding()]
param(
    [string]$BaseDir = $env:MULTI_AGENT_PIPELINE_BASE_DIR,
    [string]$Mode = "greenfield"
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
$env:PIPELINE__PIPELINE_MODE = $Mode
$env:PYTHONPATH = "$root\src"

# 1. Locate or create virtual environment
$VenvDir = Join-Path $root ".venv"
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
python -m pip install -r (Join-Path $root "requirements.txt") | Out-Null

Write-Host "MULTI_AGENT_PIPELINE_BASE_DIR = $env:MULTI_AGENT_PIPELINE_BASE_DIR"
Write-Host "PIPELINE__PIPELINE_MODE       = $env:PIPELINE__PIPELINE_MODE"
Write-Host "AGENT_MOCK                    = $env:AGENT_MOCK"

# 4. Registry readiness check
$RegistryCheck = python -c "from registry import REGISTRY; print('phases:', len(REGISTRY.list_phases()), 'agents:', len(REGISTRY.list_agents()))"
if ($LASTEXITCODE -ne 0) {
    throw "Registry readiness check failed."
}
Write-Host "Registry ready: $RegistryCheck"

# 5. Show unified CLI help
Write-Host ""
Write-Host "=== pipeline.py help ==="
python (Join-Path $root "src\pipeline.py") --help
Write-Host ""
Write-Host "=== bridge_cli.py help ==="
python (Join-Path $root "src\bridge_cli.py") --help

Write-Host ""
Write-Host "Start complete. Run the API with .\scripts\start-api.ps1"
