import json
from types import SimpleNamespace

import httpx
from openai import OpenAI

from domain.ai import AIInvestigationResult, FunctionCallOutput
from integrations.responses_gateway import ResponsesGateway
from services.tool_registry import InvestigationToolRegistry
from tests.fakes import final_result


class FakeSDKResponses:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        call = SimpleNamespace(
            type="function_call",
            call_id="call-sdk-1",
            name="get_disk_usage",
            arguments='{"device_id":"WS-002"}',
        )
        return SimpleNamespace(
            id=f"resp-sdk-{len(self.requests)}",
            model="sdk-test-model",
            output=[call] if len(self.requests) == 1 else [],
            output_text="",
            usage=SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5),
        )


def gateway_with_client(client) -> ResponsesGateway:
    return ResponsesGateway(
        api_key="unused-test-value",
        model="sdk-test-model",
        tools=InvestigationToolRegistry().openai_tools,
        max_retries=0,
        timeout_seconds=60,
        max_output_tokens=2000,
        client=client,
    )


def test_final_structured_output_closes_every_object_schema() -> None:
    schema = AIInvestigationResult.model_json_schema()
    object_schemas: list[dict] = []
    pending: list[object] = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("type") == "object":
                object_schemas.append(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)

    assert object_schemas
    assert all(item.get("additionalProperties") is False for item in object_schemas)


def test_gateway_uses_current_responses_continuation_shape() -> None:
    responses = FakeSDKResponses()
    gateway = gateway_with_client(SimpleNamespace(responses=responses))
    first = gateway.create_initial("incident")
    gateway.continue_with_outputs(
        first.response_id,
        [FunctionCallOutput(call_id="call-sdk-1", output='{"success":true}')],
    )
    assert first.function_calls[0].call_id == "call-sdk-1"
    assert all(request["max_output_tokens"] == 2000 for request in responses.requests)
    continuation = responses.requests[1]
    assert continuation["previous_response_id"] == "resp-sdk-1"
    assert continuation["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call-sdk-1",
            "output": '{"success":true}',
        }
    ]
    result_schema = continuation["text"]["format"]["schema"]
    assert continuation["text"]["format"]["type"] == "json_schema"
    assert result_schema["additionalProperties"] is False


def test_gateway_constructs_sdk_with_retry_and_timeout_controls(monkeypatch) -> None:
    captured: dict = {}
    fake_client = SimpleNamespace(responses=FakeSDKResponses())

    def client_factory(**kwargs):
        captured.update(kwargs)
        return fake_client

    monkeypatch.setattr("integrations.responses_gateway.OpenAI", client_factory)

    gateway = ResponsesGateway(
        api_key="unused-test-value",
        model="sdk-test-model",
        tools=InvestigationToolRegistry().openai_tools,
        max_retries=0,
        timeout_seconds=60,
        max_output_tokens=2000,
    )
    gateway.create_initial("incident")

    assert captured["api_key"] == "unused-test-value"
    assert captured["max_retries"] == 0
    assert captured["timeout"] == 60


def test_installed_sdk_serializes_bounded_contract_without_external_traffic() -> None:
    captured_requests: list[dict] = []

    def response_payload(response_id: str, output: list[dict]) -> dict:
        return {
            "id": response_id,
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "max_output_tokens": 2000,
            "model": "gpt-4.1-mini",
            "output": output,
            "parallel_tool_calls": True,
            "previous_response_id": "resp-sdk-1" if response_id == "resp-sdk-2" else None,
            "reasoning": None,
            "store": True,
            "temperature": 1.0,
            "text": {"format": {"type": "text"}},
            "tool_choice": "auto",
            "tools": [],
            "top_p": 1.0,
            "truncation": "disabled",
            "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            "metadata": {},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(json.loads(request.content))
        if len(captured_requests) == 1:
            output = [
                {
                    "id": "fc-sdk-1",
                    "type": "function_call",
                    "call_id": "call-sdk-1",
                    "name": "get_disk_usage",
                    "arguments": '{"device_id":"WS-002"}',
                    "status": "completed",
                }
            ]
            return httpx.Response(200, json=response_payload("resp-sdk-1", output))
        output = [
            {
                "id": "msg-sdk-1",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": final_result(),
                        "annotations": [],
                    }
                ],
            }
        ]
        return httpx.Response(200, json=response_payload("resp-sdk-2", output))

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = OpenAI(
        api_key="not-a-real-key",
        max_retries=0,
        timeout=60,
        http_client=http_client,
    )
    gateway = ResponsesGateway(
        api_key="unused-test-value",
        model="gpt-4.1-mini",
        tools=InvestigationToolRegistry().openai_tools,
        max_retries=0,
        timeout_seconds=60,
        max_output_tokens=2000,
        client=client,
    )

    first = gateway.create_initial("incident")
    final = gateway.continue_with_outputs(
        first.response_id,
        [FunctionCallOutput(call_id="call-sdk-1", output='{"success":true}')],
    )

    assert client.max_retries == 0
    assert client.timeout == 60
    assert len(captured_requests) == 2
    assert all(item["max_output_tokens"] == 2000 for item in captured_requests)
    assert captured_requests[0]["text"]["format"]["strict"] is True
    assert captured_requests[0]["text"]["format"]["schema"]["additionalProperties"] is False
    assert {tool["name"] for tool in captured_requests[0]["tools"]} == set(
        InvestigationToolRegistry().names
    )
    assert captured_requests[1]["previous_response_id"] == "resp-sdk-1"
    assert captured_requests[1]["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call-sdk-1",
            "output": '{"success":true}',
        }
    ]
    assert final.output_text == final_result()
    http_client.close()
