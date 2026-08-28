import json
from typing import Any

from mcp.server import MCPServer

from integrations.mcp_tools import MCP_TOOL_NAMES
from services.tool_registry import InvestigationToolRegistry


registry = InvestigationToolRegistry()
mcp = MCPServer(
    "Agentic SupportOps",
    instructions="Read-only allowlisted access to simulated SupportOps capabilities.",
)


def _result(name: str, arguments: dict[str, str]) -> dict[str, Any]:
    _, result = registry.dispatch(name, json.dumps(arguments))
    return result.model_dump(mode="json")


def get_disk_usage(device_id: str) -> dict[str, Any]:
    return _result("get_disk_usage", {"device_id": device_id})


def check_dns_resolution(device_id: str, hostname: str) -> dict[str, Any]:
    return _result(
        "check_dns_resolution", {"device_id": device_id, "hostname": hostname}
    )


def get_application_health(application_id: str) -> dict[str, Any]:
    return _result("get_application_health", {"application_id": application_id})


mcp.tool(
    name="get_disk_usage", description=registry.description("get_disk_usage")
)(get_disk_usage)
mcp.tool(
    name="check_dns_resolution",
    description=registry.description("check_dns_resolution"),
)(check_dns_resolution)
mcp.tool(
    name="get_application_health",
    description=registry.description("get_application_health"),
)(get_application_health)


if __name__ == "__main__":
    mcp.run(transport="stdio")
