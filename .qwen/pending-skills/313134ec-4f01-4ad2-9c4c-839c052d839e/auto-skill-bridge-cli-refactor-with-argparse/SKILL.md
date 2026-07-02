---
name: bridge-cli-refactor-with-argparse
description: Refactoring bridge_cli.py to use argparse while maintaining backward compatibility and adding health checks
source: auto-skill
extracted_at: '2026-07-02T11:06:51.444Z'
---

# Bridge CLI Refactor with Argparse

## Overview
This skill documents the approach to refactoring `bridge_cli.py` to use argparse for command-line argument parsing while maintaining backward compatibility with positional arguments and adding health checks for endpoint availability.

## Steps

### 1. Analyze Current Implementation
- Examine the existing `bridge_cli.py` to understand current command structure
- Identify all subcommands and their parameters
- Note which commands need to be preserved for backward compatibility

### 2. Create New Argparse Structure
- Define a new `build_parser()` function using `argparse.ArgumentParser`
- Create subparsers for each command (load, route, suggest, full, check-hermes, dispatch, init, advance, status, resume, etc.)
- Define both named arguments (`--project`, `--task-type`, etc.) and positional arguments for backward compatibility

### 3. Maintain Backward Compatibility
- Add positional arguments alongside named arguments for each command
- Implement logic in the main function to prioritize named arguments but fall back to positional arguments when named ones aren't provided
- Example implementation:
```python
# Use positional if named is not provided
if hasattr(args, 'project_pos') and args.project_pos and not args.project:
    args.project = args.project_pos
```

### 4. Implement Health Check Function
- Create `check_endpoint_availability(adapter_name)` function
- Check if CLI path exists using `shutil.which()`
- Test `--version` command execution
- Validate API key availability from environment variables
- Return structured result with issues and suggestions for fixes

### 5. Integrate Pipeline Commands
- Import necessary functions from `pipeline.py`
- Map new argparse commands to existing pipeline functions
- Wrap pipeline function results in consistent JSON output format

### 6. Add Dispatch Command with Health Checks
- Implement the dispatch command to call `check_endpoint_availability()` before executing
- Return health check results if validation fails
- Execute dispatch only if all health checks pass

### 7. Update Main Function Logic
- Parse arguments using the new argparse structure
- Resolve positional vs named arguments
- Validate required arguments for each command
- Map commands to their respective functions
- Output results in JSON format for consistency

### 8. Testing
- Run existing test suite to ensure backward compatibility
- Create new tests to verify argparse functionality
- Test both named and positional argument usage
- Verify health check functionality with dummy endpoints
- Confirm all pipeline commands work through the new interface

## Key Implementation Details

### Handling Both Positional and Named Arguments
```python
# Define both positional and named arguments
parser.add_argument("project_pos", nargs="?", help="Project name (positional, for backward compatibility)")
parser.add_argument("--project", help="Project name")

# In main function, prioritize named over positional
if hasattr(args, 'project_pos') and args.project_pos and not args.project:
    args.project = args.project_pos
```

### Health Check Structure
```python
def check_endpoint_availability(adapter_name: str) -> Dict[str, Any]:
    result = {
        "adapter": adapter_name,
        "cli_exists": False,
        "version_works": False,
        "api_key_valid": False,
        "issues": [],
        "suggestions": []
    }
    # Implementation details...
    return result
```

### Integration with Pipeline Functions
```python
elif args.command == "init":
    # Convert args to namespace that pipeline expects
    pipeline_args = argparse.Namespace(
        project=args.project,
        description=args.description,
        stack=args.stack,
        force=args.force
    )
    result = {"command": "init", "return_code": pipeline_cmd_init(pipeline_args)}
```

## Benefits
- Improved command-line interface with clear help and validation
- Maintained backward compatibility with existing scripts
- Added health checks for better error reporting
- Unified interface for both bridge and pipeline commands
- Consistent JSON output format for all commands