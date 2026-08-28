from dataclasses import replace
import json
from types import SimpleNamespace

import anyio
import pytest
from mcp import Client
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core.config import Settings
from integrations.mcp_client import (
    MCPInvestigationToolRegistry,
    MCPToolTransportError,
    build_investigation_tools,
)
from integrations.mcp_tools import MCP_TOOL_NAMES
from integrations.mcp_server import mcp as mcp_server
from integrations.agents_sdk_runtime import AgentsSDKRunContext, build_agents_sdk_tools
from main import app
from api.dependencies import get_responses_gateway, get_trace_boundary
from observability.tracing import TraceBoundary
from services.tool_registry import InvestigationToolRegistry
from tests.fakes import FakeResponsesGateway, call_turn, final_turn


PARITY_CASES = (
    ("get_disk_usage", {"device_id": "WS-002"}),
    (
        "check_dns_resolution",
        {"device_id": "WS-003", "hostname": "portal.contoso.example"},
    ),
    ("get_application_health", {"application_id": "SUPPORT-API"}),
)


@pytest.fixture(scope="module")
def mcp_tools() -> MCPInvestigationToolRegistry:
    return MCPInvestigationToolRegistry(timeout_seconds=10)


def test_mcp_server_exposes_exact_allowlist_and_descriptions(mcp_tools) -> None:
    assert mcp_tools.list_tools() == MCP_TOOL_NAMES
    assert set(mcp_tools.names) == set(MCP_TOOL_NAMES)
    assert {item["name"] for item in mcp_tools.openai_tools} == set(MCP_TOOL_NAMES)


def test_sdk_rejects_unknown_tool_and_invalid_input() -> None:
    async def invoke() -> tuple[bool, bool]:
        async with Client(mcp_server) as client:
            unknown = await client.call_tool("get_user", {"reference": "USR-ALICE"})
            invalid = await client.call_tool("get_disk_usage", {"device_id": 42})
            return unknown.is_error, invalid.is_error

    assert anyio.run(invoke) == (True, True)


@pytest.mark.parametrize(("name", "arguments"), PARITY_CASES)
def test_direct_and_mcp_results_are_semantically_equal(
    mcp_tools, name: str, arguments: dict[str, str]
) -> None:
    direct = InvestigationToolRegistry().execute(name, arguments)
    via_mcp = mcp_tools.call_tool(name, arguments)
    assert via_mcp == direct


def test_domain_failure_is_structured_and_does_not_poison_next_call(mcp_tools) -> None:
    failed = mcp_tools.call_tool("get_disk_usage", {"device_id": "WS-999"})
    assert failed.success is False
    assert failed.error.code == "resource_not_found"
    recovered = mcp_tools.call_tool("get_disk_usage", {"device_id": "WS-002"})
    assert recovered.success is True


def test_invalid_and_non_allowlisted_dispatch_never_reaches_mcp(
    mcp_tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        mcp_tools,
        "call_tool",
        lambda *args, **kwargs: pytest.fail("MCP subprocess must not start"),
    )
    _, invalid = mcp_tools.dispatch("get_disk_usage", '{"device_id":42}')
    _, unknown = mcp_tools.dispatch("get_user", '{"reference":"USR-ALICE"}')
    assert invalid.error.code == "malformed_arguments"
    assert unknown.error.code == "unknown_tool"
    assert mcp_tools.execute("get_user", {"reference": "USR-ALICE"}).error.code == (
        "unknown_tool"
    )


def test_subprocess_startup_failure_is_controlled() -> None:
    client = MCPInvestigationToolRegistry(
        python_executable="C:\\definitely-missing\\python.exe",
        timeout_seconds=1,
    )
    with pytest.raises(MCPToolTransportError, match="MCP tool execution failed"):
        client.list_tools()


def test_unavailable_server_module_is_controlled() -> None:
    client = MCPInvestigationToolRegistry(
        server_module="integrations.server_that_does_not_exist",
        timeout_seconds=2,
    )
    with pytest.raises(MCPToolTransportError, match="MCP tool execution failed"):
        client.list_tools()


def test_timeout_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowClient:
        closed = False

        def __init__(self, transport) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            SlowClient.closed = True

        async def call_tool(self, name, arguments):
            await anyio.sleep(1)

    monkeypatch.setattr("integrations.mcp_client.Client", SlowClient)
    client = MCPInvestigationToolRegistry(timeout_seconds=0.01)
    with pytest.raises(MCPToolTransportError, match="timed out"):
        client.call_tool("get_disk_usage", {"device_id": "WS-002"})
    assert SlowClient.closed is True


def test_malformed_mcp_result_is_controlled(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeClient:
        def __init__(self, transport) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                is_error=False,
                structured_content={"unexpected": "shape"},
            )

    monkeypatch.setattr("integrations.mcp_client.Client", FakeClient)
    client = MCPInvestigationToolRegistry()
    with pytest.raises(MCPToolTransportError, match="invalid result"):
        client.call_tool("get_disk_usage", {"device_id": "WS-002"})


def test_direct_is_default_and_mcp_is_explicit() -> None:
    direct = build_investigation_tools(Settings(tool_transport="direct"))
    mcp = build_investigation_tools(Settings(tool_transport="mcp"))
    assert type(direct) is InvestigationToolRegistry
    assert direct.transport == "direct"
    assert isinstance(mcp, MCPInvestigationToolRegistry)
    assert mcp.transport == "mcp"


def test_invalid_transport_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="TOOL_TRANSPORT"):
        Settings(tool_transport="remote")


def test_responses_opt_in_uses_mcp_and_preserves_event_timeline(
    seeded_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import routes

    monkeypatch.setattr(
        routes,
        "settings",
        replace(routes.settings, tool_transport="mcp", mcp_timeout_seconds=10),
    )
    gateway = FakeResponsesGateway(
        [
            call_turn(
                "mcp-tools",
                ("mcp-call", "get_disk_usage", {"device_id": "WS-002"}),
            ),
            final_turn(),
        ]
    )
    exporter = InMemorySpanExporter()
    boundary = TraceBoundary(enabled=True, exporter="none", span_exporter=exporter)
    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    app.dependency_overrides[get_trace_boundary] = lambda: boundary
    try:
        response = seeded_client.post("/incidents/INC-014/investigate-ai")
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)
        app.dependency_overrides.pop(get_trace_boundary, None)
        boundary.shutdown()

    assert response.status_code == 200, response.text
    events = seeded_client.get(
        "/incidents/INC-014/investigations/manual_responses/events"
    ).json()
    completed_events = [
        item for item in events if item["event_type"] == "tool_completed"
    ]
    assert len(completed_events) == 1
    completed = completed_events[0]
    assert completed["metadata"]["transport"] == "mcp"
    assert completed["tool_name"] == "get_disk_usage"
    tool_span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "supportops.tool.execute"
    )
    assert tool_span.attributes["supportops.tool.transport"] == "mcp"
    assert tool_span.attributes["mcp.transport"] == "stdio"


def test_agents_sdk_opt_in_dispatches_through_mcp_without_model_call(mcp_tools) -> None:
    class RecordingRepository:
        def __init__(self) -> None:
            self.calls = []

        def record_result(self, incident_id, result, origin, arguments) -> None:
            self.calls.append((incident_id, result, origin, arguments))

    class RecordingEvents:
        def __init__(self) -> None:
            self.items = []

        def record(self, event_type, **fields) -> None:
            self.items.append((event_type, fields))

    repository = RecordingRepository()
    events = RecordingEvents()
    context = AgentsSDKRunContext(
        repository=repository,
        tools=mcp_tools,
        incident_id=14,
        max_tool_calls=2,
        max_identical_tool_calls=2,
        events=events,
    )

    output = json.loads(
        context.execute(
            "get_disk_usage", '{"device_id":"WS-002"}', "mcp-agent-call"
        )
    )
    assert output["data"]["disk_percent"] == 98.4
    assert repository.calls[0][3] == {"device_id": "WS-002"}
    assert events.items[-1][1]["metadata"]["transport"] == "mcp"
    assert {tool.name for tool in build_agents_sdk_tools(mcp_tools)} == set(
        MCP_TOOL_NAMES
    )
