"""Bounded container logs using the official MCP SDK and Kubernetes backend."""

from functools import lru_cache
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP
from agent.kubernetes import KubernetesReader

server = FastMCP("incidentpilot-logs")


@lru_cache(maxsize=1)
def reader():
    return KubernetesReader()


@server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def search_logs(service: str, since_minutes: int, contains: str | None = None) -> dict[str, Any]:
    """Read bounded demo container logs; report unstarted containers explicitly."""
    return reader().search_logs(service, since_minutes, contains)


if __name__ == "__main__":
    server.run(transport="stdio")
