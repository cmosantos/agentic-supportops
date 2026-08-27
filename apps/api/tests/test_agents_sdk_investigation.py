from collections.abc import AsyncIterator
import socket
from typing import Any

import pytest
import httpx2
import openai
from agents import Model, ModelSettings
from agents.agent_output import AgentOutputSchema
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import ModelTracing
from agents.usage import Usage
from fastapi.testclient import TestClient
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from api.dependencies import get_agents_sdk_model
from domain.ai import AIInvestigationResult, AIInvestigationStatus
from integrations.agents_sdk_runtime import build_supportops_agent
from main import app
from services.tool_registry import InvestigationToolRegistry


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect

    def blocked(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if host in {"127.0.0.1", "::1"}:
            return original_connect(sock, address)
        raise AssertionError("Agents SDK tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)


def structured_result() -> AIInvestigationResult:
    return AIInvestigationResult(
        status=AIInvestigationStatus.COMPLETED,
        summary="External connectivity works while DNS resolution fails.",
        diagnosis="The failure domain is DNS resolution.",
        confidence=0.9,
        supporting_evidence=["The simulated DNS lookup failed."],
        recommended_next_steps=["Review the simulated DNS server status."],
        missing_information=[],
    )


def tool_response(response_id: str = "resp-sdk-tools") -> ModelResponse:
    return ModelResponse(
        output=[
            ResponseFunctionToolCall(
                call_id="call-external",
                name="check_external_connectivity",
                arguments='{"device_id":"WS-003"}',
                type="function_call",
            ),
            ResponseFunctionToolCall(
                call_id="call-dns",
                name="check_dns_resolution",
                arguments=(
                    '{"device_id":"WS-003",'
                    '"hostname":"portal.contoso.example"}'
                ),
                type="function_call",
            ),
            ResponseFunctionToolCall(
                call_id="call-config",
                name="get_network_config",
                arguments='{"device_id":"WS-003"}',
                type="function_call",
            ),
        ],
        usage=Usage(requests=1, input_tokens=100, output_tokens=20, total_tokens=120),
        response_id=response_id,
    )


def final_response(response_id: str = "resp-sdk-final") -> ModelResponse:
    return ModelResponse(
        output=[
            ResponseOutputMessage(
                id="msg-final",
                role="assistant",
                status="completed",
                type="message",
                content=[
                    ResponseOutputText(
                        annotations=[],
                        text=structured_result().model_dump_json(),
                        type="output_text",
                    )
                ],
            )
        ],
        usage=Usage(requests=1, input_tokens=80, output_tokens=40, total_tokens=120),
        response_id=response_id,
    )


def malformed_final_response() -> ModelResponse:
    response = final_response("resp-malformed")
    response.output[0].content[0].text = "{}"
    return response


class FakeAgentsModel(Model):
    def __init__(self, responses: list[ModelResponse] | None = None, error=None) -> None:
        self.responses = iter(responses or [])
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def get_response(
        self,
        system_instructions,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools,
        output_schema,
        handoffs,
        tracing: ModelTracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        self.calls.append(
            {
                "instructions": system_instructions,
                "input": input,
                "settings": model_settings,
                "tools": tools,
                "output_schema": output_schema,
                "tracing": tracing,
            }
        )
        if self.error is not None:
            raise self.error
        return next(self.responses)

    def stream_response(self, *args, **kwargs) -> AsyncIterator[TResponseStreamEvent]:
        raise NotImplementedError


def run_with_fake(client: TestClient, model: Model):
    app.dependency_overrides[get_agents_sdk_model] = lambda: model
    try:
        return client.post("/incidents/INC-019/investigate-agent-sdk")
    finally:
        app.dependency_overrides.pop(get_agents_sdk_model, None)


def test_agent_definition_uses_strict_output_and_explicit_tools() -> None:
    agent = build_supportops_agent(
        FakeAgentsModel(), InvestigationToolRegistry(), 2000, 60
    )
    assert agent.name == "SupportOps Investigator"
    assert agent.output_type is AIInvestigationResult
    assert len(agent.tools) == 20
    assert {tool.name for tool in agent.tools} == set(InvestigationToolRegistry().names)
    assert all(tool.strict_json_schema for tool in agent.tools)
    assert all(
        tool.params_json_schema["additionalProperties"] is False
        for tool in agent.tools
    )
    assert agent.model_settings.max_tokens == 2000
    assert agent.model_settings.timeout == 60
    assert agent.model_settings.retry is None
    assert agent.model_settings.parallel_tool_calls is True

    output_schema = AgentOutputSchema(agent.output_type)
    assert output_schema.is_strict_json_schema() is True
    assert output_schema.json_schema()["additionalProperties"] is False


def test_fake_runner_executes_real_tools_and_persists_sdk_audit(
    seeded_client: TestClient,
) -> None:
    model = FakeAgentsModel([tool_response(), final_response()])
    response = run_with_fake(seeded_client, model)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["investigation"]["mode"] == "agents_sdk"
    assert body["investigation"]["response_id"] == "resp-sdk-final"
    assert body["investigation"]["result"]["diagnosis"] == "The failure domain is DNS resolution."
    assert body["investigation"]["usage"] == {
        "input_tokens": 180,
        "output_tokens": 60,
        "total_tokens": 240,
        "response_iterations": 2,
        "requests": 2,
        "runtime": "agents_sdk",
        "final_agent": "SupportOps Investigator",
    }
    assert [step["tool"] for step in body["steps"]] == [
        "check_external_connectivity",
        "check_dns_resolution",
        "get_network_config",
    ]
    assert all(step["origin"] == "agents_sdk" for step in body["steps"])
    assert all(item["origin"] == "agents_sdk" for item in body["evidence"])
    assert model.calls[0]["tracing"] is ModelTracing.DISABLED
    assert all(call["settings"].max_tokens == 2000 for call in model.calls)
    assert all(call["settings"].timeout == 60 for call in model.calls)
    assert all(call["settings"].retry is None for call in model.calls)

    stored = seeded_client.get("/incidents/INC-019/agent-sdk-investigation")
    assert stored.status_code == 200
    assert stored.json() == body


def test_sdk_endpoint_degrades_without_configuration(seeded_client: TestClient) -> None:
    app.dependency_overrides[get_agents_sdk_model] = lambda: None
    try:
        response = seeded_client.post("/incidents/INC-019/investigate-agent-sdk")
    finally:
        app.dependency_overrides.pop(get_agents_sdk_model, None)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ai_not_configured"


def test_production_model_boundary_disables_http_retries(monkeypatch) -> None:
    from api import dependencies

    monkeypatch.setattr(
        dependencies,
        "settings",
        type(
            "Settings",
            (),
            {
                "openai_api_key": "test-only-key",
                "openai_model": "configured-model",
                "openai_max_retries": 0,
                "openai_timeout_seconds": 60,
            },
        )(),
    )
    model = dependencies.get_agents_sdk_model()
    assert model is not None
    assert model.model == "configured-model"
    assert model._client.max_retries == 0
    assert model._client.timeout == 60


def test_runner_max_turns_is_mapped_and_persisted(seeded_client: TestClient) -> None:
    model = FakeAgentsModel([tool_response("resp-only-tools")])
    app.dependency_overrides[get_agents_sdk_model] = lambda: model
    from api import routes

    original = routes.settings
    routes.settings = type("Settings", (), {
        **vars(original),
        "ai_max_response_iterations": 1,
    })()
    try:
        response = seeded_client.post("/incidents/INC-019/investigate-agent-sdk")
    finally:
        routes.settings = original
        app.dependency_overrides.pop(get_agents_sdk_model, None)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_loop_limit_reached"
    stored = seeded_client.get("/incidents/INC-019/agent-sdk-investigation").json()
    assert stored["investigation"]["status"] == "failed"
    assert stored["investigation"]["usage"]["requests"] == 1
    assert stored["investigation"]["response_id"] == "resp-only-tools"


def test_total_tool_limit_stops_sdk_run(seeded_client: TestClient) -> None:
    model = FakeAgentsModel([tool_response(), final_response()])
    app.dependency_overrides[get_agents_sdk_model] = lambda: model
    from api import routes

    original = routes.settings
    routes.settings = type("Settings", (), {
        **vars(original),
        "ai_max_tool_calls": 1,
    })()
    try:
        response = seeded_client.post("/incidents/INC-019/investigate-agent-sdk")
    finally:
        routes.settings = original
        app.dependency_overrides.pop(get_agents_sdk_model, None)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_tool_limit_reached"


def test_unexpected_provider_failure_is_bounded(seeded_client: TestClient) -> None:
    response = run_with_fake(seeded_client, FakeAgentsModel(error=RuntimeError("offline")))
    assert response.status_code == 502
    assert response.json()["detail"] == {
        "code": "agents_sdk_failure",
        "message": "Agents SDK investigation failed",
    }
    assert "offline" not in response.text


def test_openai_provider_failure_is_mapped(seeded_client: TestClient) -> None:
    error = openai.APIConnectionError(
        message="test connection failure",
        request=httpx2.Request("POST", "https://example.invalid"),
    )
    response = run_with_fake(seeded_client, FakeAgentsModel(error=error))
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_provider_unavailable"


def test_model_timeout_is_mapped_without_retry(seeded_client: TestClient) -> None:
    from agents.exceptions import ModelTimeoutError

    response = run_with_fake(
        seeded_client, FakeAgentsModel(error=ModelTimeoutError(60))
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_timeout"


def test_invalid_structured_output_is_mapped(seeded_client: TestClient) -> None:
    response = run_with_fake(seeded_client, FakeAgentsModel([malformed_final_response()]))
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_invalid_result"
