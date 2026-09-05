import json
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
from services.investigation_runtime_core import (
    AIInvestigationError,
    InvestigationRunSession,
    InvestigationRuntimeCore,
    InvestigationToolLimitError,
    ground_investigation_result,
)
from observability.tracing import TraceBoundary


class ResponsesClient(Protocol):
    model: str

    def create_initial(self, incident_input: str) -> ResponsesTurn: ...

    def continue_with_outputs(
        self, previous_response_id: str, outputs: list[FunctionCallOutput]
    ) -> ResponsesTurn: ...


class AIInvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        tools: InvestigationToolRegistry,
        gateway: ResponsesClient | None,
        max_response_iterations: int,
        max_tool_calls: int,
        max_identical_tool_calls: int,
        tracing: TraceBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._tools = tools
        self._gateway = gateway
        self._max_response_iterations = max_response_iterations
        self._max_tool_calls = max_tool_calls
        self._max_identical_tool_calls = max_identical_tool_calls
        self._tracing = tracing or TraceBoundary()

    def investigate(self, incident: IncidentRecord) -> AIInvestigationExecution:
        attributes = {
            "supportops.incident_reference": incident.catalog_id or str(incident.id),
            "supportops.runtime": InvestigationRuntime.MANUAL_RESPONSES.value,
        }
        if self._gateway is not None:
            attributes["supportops.model"] = self._gateway.model
        attributes["supportops.tool.transport"] = self._tools.transport
        with self._tracing.span("supportops.investigation", attributes) as span:
            execution = self._investigate(incident)
            span.set_attribute(
                "supportops.investigation_id", execution.investigation.id
            )
            span.set_attribute(
                "supportops.investigation.status",
                execution.investigation.status.value,
            )
            return execution

    def _investigate(self, incident: IncidentRecord) -> AIInvestigationExecution:
        if self._gateway is None:
            raise AIInvestigationError("ai_not_configured", "OpenAI is not configured")
        session = InvestigationRunSession.start(
            self._repository,
            incident.id,
            self._gateway.model,
            mode="ai",
            runtime=InvestigationRuntime.MANUAL_RESPONSES,
        )
        run = session.run
        events = session.events
        usage = ProviderUsage()
        last_response_id: str | None = None
        try:
            turn = self._model_turn(
                events, 1, lambda: self._gateway.create_initial(self._incident_input(incident))
            )
            iterations = 1
            usage = self._add_usage(usage, turn.usage)
            last_response_id = turn.response_id
            governance = InvestigationRuntimeCore(
                repository=self._repository,
                tools=self._tools,
                incident_id=incident.id,
                investigation_id=run.id,
                runtime=InvestigationRuntime.MANUAL_RESPONSES,
                origin=InvestigationOrigin.AI,
                max_tool_calls=self._max_tool_calls,
                max_identical_tool_calls=self._max_identical_tool_calls,
                events=events,
                tracing=self._tracing,
            )

            while turn.function_calls:
                if iterations >= self._max_response_iterations:
                    raise AIInvestigationError("ai_loop_limit_reached", "Maximum Responses iterations reached")
                outputs: list[FunctionCallOutput] = []
                for call in turn.function_calls:
                    try:
                        governed = governance.execute(
                            call.name,
                            call.arguments,
                            call.call_id,
                            iterations,
                        )
                    except InvestigationToolLimitError as error:
                        raise AIInvestigationError(error.code, str(error)) from error
                    outputs.append(
                        FunctionCallOutput(
                            call_id=call.call_id,
                            output=governed.output,
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
            result = ground_investigation_result(
                self._repository,
                incident.id,
                run.id,
                InvestigationOrigin.AI,
                result,
            )
            completed = session.complete(
                result,
                turn.response_id,
                usage,
                turn.model,
                iterations,
            )
            return self._execution(incident.id, completed)
        except ResponsesProviderError as error:
            session.fail(error.code, str(error), last_response_id, usage)
            raise AIInvestigationError(error.code, str(error)) from error
        except AIInvestigationError as error:
            session.fail(error.code, str(error), last_response_id, usage)
            raise
        except Exception as error:
            session.fail(
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
                for item in self._repository.list_evidence(
                    incident_id, InvestigationOrigin.AI, record.id
                )
            ],
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(
                    incident_id, InvestigationOrigin.AI, record.id
                )
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

    def _model_turn(
        self,
        events: InvestigationEventRecorder,
        model_turn: int,
        invoke,
    ) -> ResponsesTurn:
        with self._tracing.span(
            "supportops.model.turn",
            {
                "supportops.runtime": InvestigationRuntime.MANUAL_RESPONSES.value,
                "supportops.model": self._gateway.model,
                "supportops.model_turn": model_turn,
            },
        ) as model_span:
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
            model_span.set_attribute("supportops.response_id", turn.response_id)
            model_span.set_attribute(
                "supportops.input_tokens", turn.usage.input_tokens
            )
            model_span.set_attribute(
                "supportops.output_tokens", turn.usage.output_tokens
            )
            model_span.set_attribute(
                "supportops.total_tokens", turn.usage.total_tokens
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
