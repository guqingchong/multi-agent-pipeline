$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& .venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$root\src"

# Ensure development tooling (ruff, mypy, pytest) is installed.
# In CI or after a fresh clone, run: pip install -r requirements-dev.txt
python -m pip install -r requirements-dev.txt -q

pytest tests/ -q --tb=short
python -m ruff check src tests
python -m mypy src --ignore-missing-imports
python src/bridge_cli.py check-hermes --task-type code
