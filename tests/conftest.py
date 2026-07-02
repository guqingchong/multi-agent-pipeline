"""Auto-set AGENT_MOCK for adapter tests — real CLI tools may not be installed."""
import os
import pytest

@pytest.fixture(autouse=True)
def set_agent_mock():
    """Ensure AGENT_MOCK=true for all tests so they pass without real CLI tools."""
    os.environ["AGENT_MOCK"] = "true"
    yield
