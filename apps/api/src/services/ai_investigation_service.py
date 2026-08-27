import json
from collections import Counter
from time import monotonic
from typing import Protocol

from pydantic import ValidationError

from db.models import IncidentRecord
from domain.ai import (
    AIInvestigationExecution,
    AIInvestigationRead,
    AIInvestigationResult,
    FunctionCallOutput,
    ProviderUsage,
    ResponsesTurn,
    InvestigationEventType,
    InvestigationRuntime,
)
from domain.investigation import (
    EvidenceRead,
    InvestigationOrigin,
    InvestigationStepRead,
)
from integrations.responses_gateway import ResponsesProviderError
from repositories.investigation_repository import InvestigationRepository
from services.tool_registry import InvestigationToolRegistry
from services.investigation_event_recorder import InvestigationEventRecorder


class ResponsesClient(Protocol):
    model: str

    def create_initial(self, incident_input: str) -> ResponsesTurn: ...

    def continue_with_outputs(
        self, previous_response_id: str, outputs: list[FunctionCallOutput]
    ) -> ResponsesTurn: ...


class AIInvestigationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AIInvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        tools: InvestigationToolRegistry,
        gateway: ResponsesClient | None,
        max_response_iterations: int,
        max_tool_calls: int,
        max_identical_tool_calls: int,
    ) -> None:
        self._repository = repository
        self._tools = tools
        self._gateway = gateway
        self._max_response_iterations = max_response_iterations
        self._max_tool_calls = max_tool_calls
        self._max_identical_tool_calls = max_identical_tool_calls

    def investigate(self, incident: IncidentRecord) -> AIInvestigationExecution:
        if self._gateway is None:
            raise AIInvestigationError("ai_not_configured", "OpenAI is not configured")
        self._repository.replace_start(incident.id, InvestigationOrigin.AI)
        run = self._repository.start_ai_run(incident.id, self._gateway.model)
        events = InvestigationEventRecorder(
            self._repository, run.id, InvestigationRuntime.MANUAL_RESPONSES
        )
        run_started = monotonic()
        events.record(
            InvestigationEventType.RUN_STARTED,
            model=self._gateway.model,
            status="running",
        )
        usage = ProviderUsage()
        last_response_id: str | None = None
        try:
            turn = self._model_turn(
                events, 1, lambda: self._gateway.create_initial(self._incident_input(incident))
            )
            iterations = 1
            total_tool_calls = 0
            repeated_calls: Counter[str] = Counter()
            usage = self._add_usage(usage, turn.usage)
            last_response_id = turn.response_id

            while turn.function_calls:
                if iterations >= self._max_response_iterations:
                    raise AIInvestigationError("ai_loop_limit_reached", "Maximum Responses iterations reached")
                outputs: list[FunctionCallOutput] = []
                for call in turn.function_calls:
                    total_tool_calls += 1
                    if total_tool_calls > self._max_tool_calls:
                        raise AIInvestigationError("ai_tool_limit_reached", "Maximum total tool calls reached")
                    signature = f"{call.name}:{call.arguments}"
                    repeated_calls[signature] += 1
                    if repeated_calls[signature] > self._max_identical_tool_calls:
                        raise AIInvestigationError("ai_repeated_call_limit", "Repeated identical tool-call limit reached")
                    tool_started = monotonic()
                    events.record(
                        InvestigationEventType.TOOL_STARTED,
                        model_turn=iterations,
                        tool_name=call.name,
                        tool_call_id=call.call_id,
                        status="running",
                    )
                    try:
                        arguments, result = self._tools.dispatch(call.name, call.arguments)
                    except Exception:
                        events.record(
                            InvestigationEventType.TOOL_FAILED,
                            model_turn=iterations,
                            tool_name=call.name,
                            tool_call_id=call.call_id,
                            status="failed",
                            duration_ms=(monotonic() - tool_started) * 1000,
                        )
                        raise
                    self._repository.record_result(
                        incident.id,
                        result,
                        origin=InvestigationOrigin.AI,
                        arguments=arguments,
                    )
                    events.record(
                        InvestigationEventType.TOOL_COMPLETED if result.success else InvestigationEventType.TOOL_FAILED,
                        model_turn=iterations,
                        tool_name=call.name,
                        tool_call_id=call.call_id,
                        arguments=arguments,
                        result_summary=f"{result.tool} returned {'evidence' if result.success else 'an application error'} for {result.resource}",
                        status="completed" if result.success else "failed",
                        duration_ms=(monotonic() - tool_started) * 1000,
                    )
                    outputs.append(
                        FunctionCallOutput(
                            call_id=call.call_id,
                            output=result.model_dump_json(),
                        )
                    )
                turn = self._model_turn(
                    events,
                    iterations + 1,
                    lambda: self._gateway.continue_with_outputs(turn.response_id, outputs),
                )
                iterations += 1
                usage = self._add_usage(usage, turn.usage)
                last_response_id = turn.response_id

            if not turn.output_text:
                raise AIInvestigationError("ai_invalid_response", "OpenAI returned no final structured result")
            try:
                result = AIInvestigationResult.model_validate_json(turn.output_text)
            except ValidationError as error:
                raise AIInvestigationError("ai_invalid_result", "OpenAI returned an invalid investigation result") from error
            events.record(
                InvestigationEventType.FINAL_OUTPUT,
                model_turn=iterations,
                response_id=turn.response_id,
                status=result.status.value,
                metadata={"confidence": result.confidence},
            )
            completed = self._repository.complete_ai_run(
                run, result, turn.response_id, usage
            )
            events.record(
                InvestigationEventType.RUN_COMPLETED,
                model_turn=iterations,
                response_id=turn.response_id,
                model=turn.model,
                status=result.status.value,
                duration_ms=(monotonic() - run_started) * 1000,
            )
            return self._execution(incident.id, completed)
        except ResponsesProviderError as error:
            events.record(
                InvestigationEventType.RUN_FAILED,
                response_id=last_response_id,
                status="failed",
                duration_ms=(monotonic() - run_started) * 1000,
                metadata={"error_code": error.code},
            )
            self._repository.fail_ai_run(run, error.code, str(error), last_response_id, usage)
            raise AIInvestigationError(error.code, str(error)) from error
        except AIInvestigationError as error:
            events.record(
                InvestigationEventType.RUN_FAILED,
                response_id=last_response_id,
                status="failed",
                duration_ms=(monotonic() - run_started) * 1000,
                metadata={"error_code": error.code},
            )
            self._repository.fail_ai_run(run, error.code, str(error), last_response_id, usage)
            raise
        except Exception as error:
            events.record(
                InvestigationEventType.RUN_FAILED,
                response_id=last_response_id,
                status="failed",
                duration_ms=(monotonic() - run_started) * 1000,
                metadata={"error_code": "ai_investigation_failure"},
            )
            self._repository.fail_ai_run(
                run,
                "ai_investigation_failure",
                "AI investigation failed",
                last_response_id,
                usage,
            )
            raise AIInvestigationError(
                "ai_investigation_failure", "AI investigation failed"
            ) from error

    def get_latest(self, incident_id: int) -> AIInvestigationExecution | None:
        record = self._repository.get_ai_run(incident_id)
        return self._execution(incident_id, record) if record else None

    def _execution(self, incident_id: int, record) -> AIInvestigationExecution:
        return AIInvestigationExecution(
            investigation=AIInvestigationRead.model_validate(record),
            evidence=[
                EvidenceRead.model_validate(item)
                for item in self._repository.list_evidence(incident_id, InvestigationOrigin.AI)
            ],
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(incident_id, InvestigationOrigin.AI)
            ],
        )

    @staticmethod
    def _incident_input(incident: IncidentRecord) -> str:
        payload = {
            "catalog_id": incident.catalog_id,
            "title": incident.title,
            "description": incident.description,
            "category": incident.category,
            "priority": incident.priority.value,
            "affected_resource_type": incident.affected_resource_type,
            "affected_resource_id": incident.affected_resource_id,
            "investigation_context": incident.investigation_context,
        }
        return "Investigate this incident. Its title is a symptom label, not proof:\n" + json.dumps(payload)

    @staticmethod
    def _add_usage(current: ProviderUsage, added: ProviderUsage) -> ProviderUsage:
        return ProviderUsage(
            input_tokens=current.input_tokens + added.input_tokens,
            output_tokens=current.output_tokens + added.output_tokens,
            total_tokens=current.total_tokens + added.total_tokens,
            response_iterations=current.response_iterations + 1,
            requests=current.requests + 1,
            runtime="manual_responses",
        )

    @staticmethod
    def _model_turn(
        events: InvestigationEventRecorder,
        model_turn: int,
        invoke,
    ) -> ResponsesTurn:
        events.record(
            InvestigationEventType.MODEL_TURN_STARTED,
            model_turn=model_turn,
            status="running",
        )
        started = monotonic()
        try:
            turn = invoke()
        except Exception:
            events.record(
                InvestigationEventType.MODEL_TURN_COMPLETED,
                model_turn=model_turn,
                status="failed",
                duration_ms=(monotonic() - started) * 1000,
            )
            raise
        for call in turn.function_calls:
            try:
                arguments = json.loads(call.arguments)
            except json.JSONDecodeError:
                arguments = None
            events.record(
                InvestigationEventType.TOOL_REQUESTED,
                model_turn=model_turn,
                tool_name=call.name,
                tool_call_id=call.call_id,
                arguments=arguments,
                status="requested",
            )
        events.record(
            InvestigationEventType.MODEL_TURN_COMPLETED,
            model_turn=model_turn,
            response_id=turn.response_id,
            model=turn.model,
            input_tokens=turn.usage.input_tokens,
            output_tokens=turn.usage.output_tokens,
            total_tokens=turn.usage.total_tokens,
            duration_ms=(monotonic() - started) * 1000,
            status="completed",
        )
        return turn
