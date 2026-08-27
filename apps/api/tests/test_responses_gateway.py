from types import SimpleNamespace

from domain.ai import FunctionCallOutput
from integrations.responses_gateway import ResponsesGateway
from services.tool_registry import InvestigationToolRegistry


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


def test_gateway_uses_current_responses_continuation_shape() -> None:
    responses = FakeSDKResponses()
    client = SimpleNamespace(responses=responses)
    gateway = ResponsesGateway(
        api_key="unused-test-value",
        model="sdk-test-model",
        tools=InvestigationToolRegistry().openai_tools,
        client=client,
    )
    first = gateway.create_initial("incident")
    gateway.continue_with_outputs(
        first.response_id,
        [FunctionCallOutput(call_id="call-sdk-1", output='{"success":true}')],
    )
    assert first.function_calls[0].call_id == "call-sdk-1"
    continuation = responses.requests[1]
    assert continuation["previous_response_id"] == "resp-sdk-1"
    assert continuation["input"] == [
        {
            "type": "function_call_output",
            "call_id": "call-sdk-1",
            "output": '{"success":true}',
        }
    ]
    assert continuation["text"]["format"]["type"] == "json_schema"

