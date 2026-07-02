#!/usr/bin/env python3
"""Test script for new bridge_cli features"""

import json
import subprocess
import sys
from pathlib import Path

def run_command(cmd):
    """Run a command and return the result"""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr

def test_help():
    """Test that help works"""
    print("Testing help...")
    code, out, err = run_command("cd C:/tmp/multi-agent-pipeline && python src/bridge_cli.py --help")
    assert code == 0, f"Help command failed: {err}"
    assert "dispatch" in out, "dispatch command not in help"
    print("✓ Help works")

def test_health_check():
    """Test health check functionality"""
    print("Testing health check...")
    code, out, err = run_command("cd C:/tmp/multi-agent-pipeline && python src/bridge_cli.py dispatch dummy code test --timeout 30")
    assert code == 0, f"Dispatch command failed: {err}"
    
    # Find the JSON part in the output
    lines = out.strip().split('\n')
    # Look for the first line that starts with { and the last line that ends with }
    start_idx = -1
    end_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('{'):
            start_idx = i
        if line.strip().endswith('}') and start_idx != -1:
            end_idx = i
    
    assert start_idx != -1 and end_idx != -1, "Could not find JSON in output"
    
    json_str = '\n'.join(lines[start_idx:end_idx+1])
    result = json.loads(json_str)
    assert result["command"] == "dispatch"
    assert result["error"] == "Health check failed"
    assert result["health_check"]["adapter"] == "dummy"
    print("✓ Health check works")

def extract_json_from_output(output):
    """Extract JSON object from command output"""
    lines = output.strip().split('\n')
    # Look for the first line that starts with { and the last line that ends with }
    start_idx = -1
    end_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('{'):
            start_idx = i
        if stripped.endswith('}') and start_idx != -1:
            end_idx = i
    
    if start_idx != -1 and end_idx != -1:
        json_str = '\n'.join(lines[start_idx:end_idx+1])
        return json.loads(json_str)
    
    raise ValueError(f"Could not find JSON in output: {output}")

def test_backward_compatibility():
    """Test backward compatibility with positional arguments"""
    print("Testing backward compatibility...")

    # Test suggest with positional argument
    code, out, err = run_command("cd C:/tmp/multi-agent-pipeline && python src/bridge_cli.py suggest test-project")
    assert code == 0, f"Suggest command failed: {err}"
    result = extract_json_from_output(out)
    assert result["command"] == "suggest"
    assert result["project"] == "test-project"
    print("✓ Backward compatibility works")

def test_named_arguments():
    """Test named arguments"""
    print("Testing named arguments...")

    # Test suggest with named argument
    code, out, err = run_command("cd C:/tmp/multi-agent-pipeline && python src/bridge_cli.py suggest --project test-project")
    assert code == 0, f"Suggest command with named arg failed: {err}"
    result = extract_json_from_output(out)
    assert result["command"] == "suggest"
    assert result["project"] == "test-project"
    print("✓ Named arguments work")

def test_pipeline_commands():
    """Test pipeline commands"""
    print("Testing pipeline commands...")

    # Test status command
    code, out, err = run_command("cd C:/tmp/multi-agent-pipeline && python src/bridge_cli.py status --project test-project")
    assert code == 0, f"Status command failed: {err}"
    
    # Split the output to get the first JSON (project state) and second JSON (command result)
    parts = out.strip().split('\n}\n{')
    if len(parts) > 1:
        # Reconstruct the first JSON object
        first_json_str = parts[0] + '}'  # Add back the closing brace
        project_state = json.loads(first_json_str.strip())
        
        assert project_state["name"] == "test-project"
        assert project_state["phase"] == "init"
    else:
        # If there's only one JSON object, use the existing extraction method
        result = extract_json_from_output(out)
        assert result["name"] == "test-project"
        assert result["phase"] == "init"
    
    print("✓ Pipeline commands work")

if __name__ == "__main__":
    print("Running tests for new bridge_cli features...")
    test_help()
    test_health_check()
    test_backward_compatibility()
    test_named_arguments()
    test_pipeline_commands()
    print("\n✓ All tests passed!")