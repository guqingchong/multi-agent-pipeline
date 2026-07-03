"""src/mcp_stdio_server.py — Minimal stdio MCP server for bridge_cli.

Implements enough of the Model Context Protocol (MCP) over stdin/stdout for
Hermes to discover and call multi-agent-pipeline tools without a persistent
HTTP service.

Supported MCP methods:
  - initialize
  - notifications/initialized
  - tools/list
  - tools/call
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional


class McpJsonRpcError(Exception):
    """Raised when an incoming JSON-RPC message is malformed."""

    def __init__(self, code: int, message: str, data: Optional[Any] = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _send_message(msg: Dict[str, Any]) -> None:
    """Write a JSON-RPC message to stdout and flush."""
    text = json.dumps(msg, ensure_ascii=False, default=str)
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _send_response(request_id: Any, result: Optional[Any] = None) -> None:
    _send_message({"jsonrpc": "2.0", "id": request_id, "result": result})


def _send_error(request_id: Any, code: int, message: str, data: Optional[Any] = None) -> None:
    error: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    _send_message({"jsonrpc": "2.0", "id": request_id, "error": error})


def _tool(name: str, description: str, properties: Dict[str, Any], required: List[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# Tool definitions exposed to MCP clients.
_TOOLS = [
    _tool(
        "pipeline_init",
        "Initialize a new project in the multi-agent pipeline.",
        {
            "project": {"type": "string", "description": "Project name"},
            "description": {"type": "string", "description": "Short project description"},
            "stack": {"type": "string", "description": "Tech stack, e.g. python"},
            "force": {"type": "boolean", "description": "Overwrite existing project"},
        },
        ["project"],
    ),
    _tool(
        "pipeline_advance",
        "Advance a project to the next phase.",
        {"project": {"type": "string", "description": "Project name"}},
        ["project"],
    ),
    _tool(
        "pipeline_status",
        "Get the current status of a project.",
        {"project": {"type": "string", "description": "Project name"}},
        ["project"],
    ),
    _tool(
        "pipeline_inspect",
        "Run an independent audit on the current or given phase.",
        {
            "project": {"type": "string", "description": "Project name"},
            "phase": {"type": "string", "description": "Phase to audit (default: current)"},
        },
        ["project"],
    ),
    _tool(
        "pipeline_check_hermes",
        "Check whether Hermes may execute a task type directly or must delegate.",
        {"task_type": {"type": "string", "description": "Task type, e.g. code, review"}},
        ["task_type"],
    ),
    _tool(
        "pipeline_route",
        "Route a task type to the appropriate agent adapter.",
        {
            "task_type": {"type": "string", "description": "Task type"},
            "feature_id": {"type": "string", "description": "Optional feature ID"},
        },
        ["task_type"],
    ),
    _tool(
        "pipeline_suggest",
        "Generate a next-step suggestion for a project.",
        {"project": {"type": "string", "description": "Project name"}},
        ["project"],
    ),
    _tool(
        "pipeline_full",
        "Run the full flow: load project state and suggest next step.",
        {"project": {"type": "string", "description": "Project name"}},
        ["project"],
    ),
    _tool(
        "pipeline_dispatch",
        "Dispatch a task to a specific agent adapter.",
        {
            "adapter": {"type": "string", "description": "Adapter name, e.g. claude-code"},
            "task_type": {"type": "string", "description": "Task type"},
            "prompt": {"type": "string", "description": "Prompt for the task"},
            "timeout": {"type": "integer", "description": "Timeout in seconds"},
            "feature_id": {"type": "string", "description": "Optional feature ID"},
        },
        ["adapter", "task_type"],
    ),
    _tool(
        "pipeline_audit_report",
        "Show inspector audit history for a project.",
        {"project": {"type": "string", "description": "Project name"}},
        ["project"],
    ),
]


def _call_tool(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatch a tool call to the matching bridge_cli command function."""
    # Import bridge_cli command functions lazily to avoid circular imports.
    import bridge_cli as cli

    try:
        if name == "pipeline_init":
            return cli.cmd_init(
                project_name=arguments["project"],
                description=arguments.get("description", ""),
                stack=arguments.get("stack", ""),
                force=arguments.get("force", False),
            )
        if name == "pipeline_advance":
            return cli.cmd_advance(arguments["project"])
        if name == "pipeline_status":
            return cli.cmd_status(arguments["project"])
        if name == "pipeline_inspect":
            return cli.cmd_inspect(arguments["project"], phase=arguments.get("phase", ""))
        if name == "pipeline_check_hermes":
            return cli.cmd_check_hermes(arguments["task_type"])
        if name == "pipeline_route":
            return cli.cmd_route(arguments["task_type"], arguments.get("feature_id", ""))
        if name == "pipeline_suggest":
            return cli.cmd_suggest(arguments["project"])
        if name == "pipeline_full":
            return cli.cmd_full(arguments["project"])
        if name == "pipeline_dispatch":
            return cli.cmd_dispatch(
                adapter=arguments["adapter"],
                task_type=arguments["task_type"],
                prompt=arguments.get("prompt", ""),
                timeout=arguments.get("timeout", 600),
                feature_id=arguments.get("feature_id", ""),
            )
        if name == "pipeline_audit_report":
            return cli.cmd_audit_report(arguments["project"])
    except Exception as exc:  # pragma: no cover - defensive
        return {"error": str(exc), "tool": name}

    return {"error": f"Unknown tool: {name}"}


def _handle_initialize(request_id: Any, params: Dict[str, Any]) -> None:
    """Respond to MCP initialize request."""
    _send_response(
        request_id,
        {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}, "logging": {}},
            "serverInfo": {"name": "multi-agent-pipeline-mcp", "version": "2.0.0"},
        },
    )


def _handle_tools_list(request_id: Any) -> None:
    """Respond to MCP tools/list request."""
    _send_response(request_id, {"tools": _TOOLS})


def _handle_tools_call(request_id: Any, params: Dict[str, Any]) -> None:
    """Execute a tool and respond to MCP tools/call request."""
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not name:
        _send_error(request_id, -32602, "Missing tool name")
        return

    result = _call_tool(name, arguments)
    _send_response(
        request_id,
        {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, default=str),
                }
            ],
            "isError": bool(result.get("error")),
        },
    )


def _dispatch_message(msg: Dict[str, Any]) -> None:
    """Route a single parsed JSON-RPC message to its handler."""
    method = msg.get("method")
    request_id = msg.get("id")
    params = msg.get("params") or {}

    if method == "initialize":
        _handle_initialize(request_id, params)
    elif method == "notifications/initialized":
        # Notification, no response required.
        pass
    elif method == "tools/list":
        _handle_tools_list(request_id)
    elif method == "tools/call":
        _handle_tools_call(request_id, params)
    else:
        if request_id is not None:
            _send_error(request_id, -32601, f"Method not found: {method}")


def run_stdio_server() -> None:
    """Run the MCP stdio server until stdin is closed."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            _send_error(None, -32700, f"Parse error: {exc}")
            continue

        if not isinstance(msg, dict):
            _send_error(None, -32600, "Invalid Request: message must be a JSON object")
            continue

        try:
            _dispatch_message(msg)
        except Exception as exc:  # pragma: no cover - defensive
            request_id = msg.get("id")
            if request_id is not None:
                _send_error(request_id, -32603, f"Internal error: {exc}")


if __name__ == "__main__":
    run_stdio_server()
