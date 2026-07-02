# env.example.ps1 — Environment variable template for Windows
#
# Copy this file to .env.ps1, customize values, then run:
#   . .\env.ps1
# before executing pipeline.py / bridge_cli.py / start-api.ps1.

# Project base directory where all projects live.
$env:MULTI_AGENT_PIPELINE_BASE_DIR = "C:\\tmp\\multi-agent-pipeline"

# Pipeline mode: greenfield (new project) or brownfield (legacy optimization).
$env:PIPELINE__PIPELINE_MODE = "greenfield"

# SQLite database filename.
$env:PIPELINE__DB_NAME = "pipeline_state.db"

# Set to true to short-circuit real agent CLI calls during tests/development.
$env:AGENT_MOCK = "true"

# Optional: override adapter CLI paths.
# $env:AGENT_CLI_PATH_CLAUDE_CODE = "C:\\Tools\\claude.cmd"
# $env:AGENT_CLI_PATH_CODEWHALE = "C:\\Tools\\codewhale-tui.exe"
# $env:AGENT_CLI_PATH_QWEN_CODE = "C:\\Tools\\qwen.cmd"

# Optional API keys (only needed when AGENT_MOCK=false).
# $env:CLAUDE_CODE_SIMPLE = "1"
# $env:QWEN_CODE_SUPPRESS_YOLO_WARNING = "1"
