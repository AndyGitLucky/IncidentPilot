"""Official MCP SDK stdio server exposing exactly four Kubernetes read tools."""

from functools import lru_cache
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.fastmcp import FastMCP
from agent.kubernetes import KubernetesReader

server = FastMCP("incidentpilot-kubernetes")


@lru_cache(maxsize=1)
def reader():
    return KubernetesReader()


@server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_pods(namespace: str) -> dict[str, Any]:
    """Read bounded pod state in the demo namespace."""
    return reader().get_pods(namespace)


@server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def describe_pod(namespace: str, pod: str) -> dict[str, Any]:
    """Read pod identity, ownership, images, and container state; excludes environment values."""
    return reader().describe_pod(namespace, pod)


@server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_events(namespace: str) -> dict[str, Any]:
    """Read demo events, retaining involved-object UIDs for evidence correlation."""
    return reader().get_events(namespace)


@server.tool(annotations={"readOnlyHint": True, "destructiveHint": False})
def get_deployment(namespace: str, name: str) -> dict[str, Any]:
    """Read deployment rollout state and owned ReplicaSet identities; excludes secrets."""
    return reader().get_deployment(namespace, name)


if __name__ == "__main__":
    server.run(transport="stdio")
