import socket

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from agents.models.interface import ModelTracing

from api.dependencies import get_trace_boundary
from integrations.responses_gateway import ResponsesProviderError
from main import app
from observability.tracing import TraceBoundary
from tests.fakes import FakeResponsesGateway, call_turn, final_turn
from tests.test_agents_sdk_investigation import (
    FakeAgentsModel,
    final_response,
    run_with_fake as run_sdk,
    tool_response,
)
from tests.test_ai_investigation import run_with_fake as run_manual


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def blocked(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if host in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("OpenTelemetry tests must not open external connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def enabled_boundary() -> tuple[TraceBoundary, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    return (
        TraceBoundary(enabled=True, exporter="none", span_exporter=exporter),
        exporter,
    )


def with_boundary(boundary: TraceBoundary):
    app.dependency_overrides[get_trace_boundary] = lambda: boundary


def clear_boundary() -> None:
    app.dependency_overrides.pop(get_trace_boundary, None)


def span_by_name(exporter: InMemorySpanExporter, name: str):
    return [span for span in exporter.get_finished_spans() if span.name == name]


def manual_gateway() -> FakeResponsesGateway:
    return FakeResponsesGateway(
        [
            call_turn(
                "resp-tools",
                (
                    "call-external",
                    "check_external_connectivity",
                    {"device_id": "WS-003"},
                ),
                (
                    "call-dns",
                    "check_dns_resolution",
                    {
                        "device_id": "WS-003",
                        "hostname": "portal.contoso.example",
                    },
                ),
                (
                    "call-config",
                    "get_network_config",
                    {"device_id": "WS-003"},
                ),
            ),
            final_turn(),
        ]
    )


def assert_investigation_hierarchy(exporter, runtime: str) -> None:
    investigations = span_by_name(exporter, "supportops.investigation")
    assert len(investigations) == 1
    investigation = investigations[0]
    assert investigation.attributes["supportops.runtime"] == runtime
    trace_id = investigation.context.trace_id
    assert trace_id != 0

    requests = span_by_name(exporter, "api.investigation.request")
    assert len(requests) == 1
    assert investigation.parent.span_id == requests[0].context.span_id

    model_spans = span_by_name(exporter, "supportops.model.turn")
    tool_spans = span_by_name(exporter, "supportops.tool.execute")
    assert len(model_spans) == 2
    assert len(tool_spans) == 3
    assert all(span.context.trace_id == trace_id for span in model_spans + tool_spans)
    assert all(
        span.parent.span_id == investigation.context.span_id
        for span in model_spans + tool_spans
    )
    assert all(span.end_time >= span.start_time for span in model_spans + tool_spans)
    assert {span.attributes["supportops.tool.name"] for span in tool_spans} == {
        "check_external_connectivity",
        "check_dns_resolution",
        "get_network_config",
    }
    assert all(
        "supportops.total_tokens" in span.attributes for span in model_spans
    )
    persistence = span_by_name(exporter, "supportops.persistence.write")
    assert persistence
    assert all(span.context.trace_id == trace_id for span in persistence)


def test_disabled_boundary_exports_nothing_and_events_remain_provider_neutral(
    seeded_client: TestClient,
) -> None:
    exporter = InMemorySpanExporter()
    boundary = TraceBoundary(
        enabled=False, exporter="none", span_exporter=exporter
    )
    with_boundary(boundary)
    try:
        response = run_manual(
            seeded_client, "INC-019", FakeResponsesGateway([final_turn()])
        )
        timeline = seeded_client.get(
            "/incidents/INC-019/investigations/manual_responses/events"
        ).json()
    finally:
        clear_boundary()
        boundary.shutdown()
    assert response.status_code == 200
    assert exporter.get_finished_spans() == ()
    assert timeline
    assert all("trace_id" not in item["metadata"] for item in timeline)
    assert all("span_id" not in item["metadata"] for item in timeline)


def test_manual_runtime_exports_correlated_parent_child_spans(
    seeded_client: TestClient,
) -> None:
    boundary, exporter = enabled_boundary()
    with_boundary(boundary)
    try:
        response = run_manual(seeded_client, "INC-019", manual_gateway())
        timeline = seeded_client.get(
            "/incidents/INC-019/investigations/manual_responses/events"
        ).json()
    finally:
        clear_boundary()
        boundary.shutdown()
    assert response.status_code == 200
    assert_investigation_hierarchy(exporter, "manual_responses")
    completed = next(
        item for item in timeline if item["event_type"] == "tool_completed"
    )
    assert len(completed["metadata"]["trace_id"]) == 32
    assert len(completed["metadata"]["span_id"]) == 16
    tool_span_ids = {
        format(span.context.span_id, "016x")
        for span in span_by_name(exporter, "supportops.tool.execute")
    }
    assert completed["metadata"]["span_id"] in tool_span_ids


def test_agents_sdk_exports_same_local_hierarchy_without_sdk_tracing(
    seeded_client: TestClient,
) -> None:
    boundary, exporter = enabled_boundary()
    model = FakeAgentsModel([tool_response(), final_response()])
    with_boundary(boundary)
    try:
        response = run_sdk(seeded_client, model)
    finally:
        clear_boundary()
        boundary.shutdown()
    assert response.status_code == 200
    assert_investigation_hierarchy(exporter, "agents_sdk")
    assert all(call["tracing"] is ModelTracing.DISABLED for call in model.calls)


def test_separate_investigations_have_separate_trace_ids(
    seeded_client: TestClient,
) -> None:
    boundary, exporter = enabled_boundary()
    with_boundary(boundary)
    try:
        first = run_manual(
            seeded_client, "INC-019", FakeResponsesGateway([final_turn()])
        )
        second = run_manual(
            seeded_client, "INC-018", FakeResponsesGateway([final_turn()])
        )
    finally:
        clear_boundary()
        boundary.shutdown()
    assert first.status_code == second.status_code == 200
    investigations = span_by_name(exporter, "supportops.investigation")
    assert len(investigations) == 2
    assert len({span.context.trace_id for span in investigations}) == 2


def test_provider_failure_marks_spans_error_without_secret_attributes(
    seeded_client: TestClient,
) -> None:
    class FailedGateway:
        model = "fake-model"

        def create_initial(self, incident_input: str):
            raise ResponsesProviderError(
                "ai_provider_unavailable", "Provider unavailable"
            )

    boundary, exporter = enabled_boundary()
    with_boundary(boundary)
    try:
        response = run_manual(seeded_client, "INC-019", FailedGateway())
    finally:
        clear_boundary()
        boundary.shutdown()
    assert response.status_code == 502
    investigation = span_by_name(exporter, "supportops.investigation")[0]
    model = span_by_name(exporter, "supportops.model.turn")[0]
    assert investigation.status.status_code is StatusCode.ERROR
    assert model.status.status_code is StatusCode.ERROR
    assert all(not span.events for span in exporter.get_finished_spans())
    all_attributes = " ".join(
        f"{key}={value}"
        for span in exporter.get_finished_spans()
        for key, value in span.attributes.items()
    ).lower()
    assert "api_key" not in all_attributes
    assert "authorization" not in all_attributes
    assert "prompt" not in all_attributes


def test_fake_sdk_tool_spans_prove_local_execution_is_sequential(
    seeded_client: TestClient,
) -> None:
    boundary, exporter = enabled_boundary()
    with_boundary(boundary)
    try:
        response = run_sdk(
            seeded_client, FakeAgentsModel([tool_response(), final_response()])
        )
    finally:
        clear_boundary()
        boundary.shutdown()
    assert response.status_code == 200
    tools = sorted(
        span_by_name(exporter, "supportops.tool.execute"),
        key=lambda span: span.start_time,
    )
    assert all(
        current.end_time <= following.start_time
        for current, following in zip(tools, tools[1:])
    )


def test_console_exporter_constructs_and_finishes_locally() -> None:
    boundary = TraceBoundary(enabled=True, exporter="console")
    with boundary.span(
        "supportops.local.validation",
        {"supportops.runtime": "fake_validation"},
    ):
        pass
    boundary.shutdown()
