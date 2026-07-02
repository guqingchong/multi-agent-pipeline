$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$root\src"
pytest tests/ -q --tb=short
python -m ruff check src tests
python -m mypy src --ignore-missing-imports
python src/bridge_cli.py check-hermes --task-type code
