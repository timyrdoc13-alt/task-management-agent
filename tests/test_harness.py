from agent.harness import AgentHarness
from agent.types import AgentContext


def test_unknown_tool():
    h = AgentHarness(AgentContext(channel="cli"))
    r = h.execute_tool("no_such_tool", {})
    assert r["status"] == "error"
    assert r["error_type"] == "unknown_tool"
