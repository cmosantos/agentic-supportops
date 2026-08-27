import json

from domain.ai import (
    AIInvestigationResult,
    AIInvestigationStatus,
    FunctionCallOutput,
    ProviderFunctionCall,
    ProviderUsage,
    ResponsesTurn,
)


def final_result(diagnosis: str = "Evidence-supported condition") -> str:
    return AIInvestigationResult(
        status=AIInvestigationStatus.COMPLETED,
        summary="The incident was investigated using read-only tools.",
        diagnosis=diagnosis,
        confidence=0.9,
        supporting_evidence=["Tool evidence supports the diagnosis."],
        recommended_next_steps=["Review the proposed remediation with an operator."],
        missing_information=[],
    ).model_dump_json()


def call_turn(response_id: str, *calls: tuple[str, str, dict[str, str]]) -> ResponsesTurn:
    return ResponsesTurn(
        response_id=response_id,
        model="fake-responses-model",
        function_calls=[
            ProviderFunctionCall(call_id=call_id, name=name, arguments=json.dumps(arguments))
            for call_id, name, arguments in calls
        ],
        usage=ProviderUsage(input_tokens=10, output_tokens=2, total_tokens=12),
    )


def final_turn(response_id: str = "resp-final", output: str | None = None) -> ResponsesTurn:
    return ResponsesTurn(
        response_id=response_id,
        model="fake-responses-model",
        output_text=output if output is not None else final_result(),
        usage=ProviderUsage(input_tokens=4, output_tokens=8, total_tokens=12),
    )


class FakeResponsesGateway:
    model = "fake-responses-model"

    def __init__(self, turns: list[ResponsesTurn]) -> None:
        self._turns = iter(turns)
        self.initial_inputs: list[str] = []
        self.continuations: list[tuple[str, list[FunctionCallOutput]]] = []

    def create_initial(self, incident_input: str) -> ResponsesTurn:
        self.initial_inputs.append(incident_input)
        return next(self._turns)

    def continue_with_outputs(
        self, previous_response_id: str, outputs: list[FunctionCallOutput]
    ) -> ResponsesTurn:
        self.continuations.append((previous_response_id, outputs))
        return next(self._turns)

