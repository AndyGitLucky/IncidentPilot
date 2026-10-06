"""Fixed local stdio MCP connections; tool arguments never become shell commands."""

from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.kubernetes import ROOT

KUBERNETES_TOOLS = {"get_pods", "describe_pod", "get_events", "get_deployment"}
LOGS_TOOLS = {"search_logs"}


class MCPTools:
    def __init__(self, kubernetes_session, logs_session):
        self.kubernetes_session = kubernetes_session
        self.logs_session = logs_session

    async def call(self, name: str, arguments: dict) -> dict:
        if name in KUBERNETES_TOOLS:
            session = self.kubernetes_session
        elif name in LOGS_TOOLS:
            session = self.logs_session
        else:
            raise ValueError("Tool is outside the M2 read allowlist")
        result = await session.call_tool(name, arguments)
        if result.isError:
            # Avoid passing SDK exception bodies or credential details to the LLM.
            raise RuntimeError(f"{name} failed; check read-only credentials and demo resources")
        if not isinstance(result.structuredContent, dict):
            raise RuntimeError(f"{name} returned no structured evidence")
        return result.structuredContent


@asynccontextmanager
async def connect_tools():
    async with AsyncExitStack() as stack:
        sessions = []
        for script in ("kubernetes_server.py", "logs_server.py"):
            parameters = StdioServerParameters(
                command=sys.executable, args=[str(ROOT / "mcp" / script)], cwd=str(ROOT),
            )
            read, write = await stack.enter_async_context(stdio_client(parameters))
            session = await stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=45),
            ))
            await session.initialize()
            sessions.append(session)
        yield MCPTools(*sessions)
