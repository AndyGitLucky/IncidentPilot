import asyncio
import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("kubernetes")

from mcp.shared.memory import create_connected_server_and_client_session

from agent.mcp_client import MCPTools


def load_server(filename):
    path = Path(__file__).resolve().parents[1] / "mcp" / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename,expected,call,args", [
    ("kubernetes_server.py", {"get_pods", "describe_pod", "get_events", "get_deployment"}, "get_pods", {"namespace": "incidentpilot"}),
    ("logs_server.py", {"search_logs"}, "search_logs", {"service": "api", "since_minutes": 10}),
])
def test_official_mcp_protocol_advertises_only_read_tools(monkeypatch, filename, expected, call, args):
    module = load_server(filename)

    class Reader:
        def get_pods(self, namespace):
            assert namespace == "incidentpilot"
            return {"pods": []}

        def search_logs(self, service, since_minutes, contains):
            assert (service, since_minutes, contains) == ("api", 10, None)
            return {"entries": []}

    monkeypatch.setattr(module, "reader", lambda: Reader())

    async def check():
        async with create_connected_server_and_client_session(module.server) as session:
            listed = await session.list_tools()
            assert {tool.name for tool in listed.tools} == expected
            assert all(tool.annotations.readOnlyHint and not tool.annotations.destructiveHint for tool in listed.tools)
            result = await session.call_tool(call, args)
            assert not result.isError
            assert result.structuredContent in ({"pods": []}, {"entries": []})
            forbidden = await session.call_tool("restart_deployment", {})
            assert forbidden.isError
    asyncio.run(check())


def test_agent_mcp_client_refuses_unknown_tools():
    with pytest.raises(ValueError, match="allowlist"):
        asyncio.run(MCPTools(None, None).call("patch_deployment", {}))
