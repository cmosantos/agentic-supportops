import json
from collections import Counter
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from domain.ai import (
    AIInvestigationResult,
    AIInvestigationStatus,
    InvestigationEventType,
    InvestigationRuntime,
    ProviderUsage,
)
from domain.investigation import InvestigationOrigin, ToolResult
from observability.tracing import TraceBoundary
from repositories.investigation_repository import InvestigationRepository
from services.investigation_event_recorder import InvestigationEventRecorder
from services.tool_registry import InvestigationToolRegistry


class InvestigationToolLimitError(RuntimeError):
    """Application-owned stop condition for governed investigation tools."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AIInvestigationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def ground_investigation_result(
    repository: InvestigationRepository,
    incident_id: int,
    investigation_id: int,
    origin: InvestigationOrigin,
    result: AIInvestigationResult,
) -> AIInvestigationResult:
    evidence_ids = [
        item.id
        for item in repository.list_evidence(incident_id, origin, investigation_id)
    ]
    update = {"evidence_ids": evidence_ids, "human_action_required": True}
    if not evidence_ids:
        update.update(
            status=AIInvestigationStatus.INSUFFICIENT_EVIDENCE,
            confidence=min(result.confidence, 0.25),
            supporting_evidence=[],
            missing_information=[
                *result.missing_information,
                "No successful tool evidence was collected for this investigation.",
            ],
        )
    return result.model_copy(update=update)


@dataclass(frozen=True)
class GovernedToolCall:
    arguments: dict[str, Any]
    result: ToolResult
    evidence_id: int | None
    output: str


@dataclass
class InvestigationRunSession:
    """Common persisted run lifecycle, with provider metadata supplied by adapters."""

    repository: InvestigationRepository
    run: Any
    events: InvestigationEventRecorder
    runtime: InvestigationRuntime
    started_at: float

    @classmethod
    def start(
        cls,
        repository: InvestigationRepository,
        incident_id: int,
        model: str,
        mode: str,
        runtime: InvestigationRuntime,
    ) -> "InvestigationRunSession":
        run = repository.start_ai_run(incident_id, model, mode=mode)
        events = InvestigationEventRecorder(repository, run.id, runtime)
        started_at = monotonic()
        events.record(InvestigationEventType.RUN_STARTED, model=model, status="running")
        return cls(repository, run, events, runtime, started_at)

    def complete(
        self,
        result: AIInvestigationResult,
        response_id: str | None,
        usage: ProviderUsage,
        model: str,
        model_turn: int | None,
        metadata: dict[str, Any] | None = None,
    ):
        self.events.record(
            InvestigationEventType.FINAL_OUTPUT,
            model_turn=model_turn,
            response_id=response_id,
            status=result.status.value,
            metadata={"confidence": result.confidence, **(metadata or {})},
        )
        self.events.record(
            InvestigationEventType.RUN_COMPLETED,
            commit=False,
            model_turn=model_turn,
            response_id=response_id,
            model=model,
            status=result.status.value,
            duration_ms=(monotonic() - self.started_at) * 1000,
            metadata=metadata or {},
        )
        return self.repository.complete_ai_run(self.run, result, response_id, usage)

    def fail(
        self,
        code: str,
        message: str,
        response_id: str | None,
        usage: ProviderUsage,
    ) -> None:
        self.events.record(
            InvestigationEventType.RUN_FAILED,
            commit=False,
            response_id=response_id,
            status="failed",
            duration_ms=(monotonic() - self.started_at) * 1000,
            metadata={"error_code": code},
        )
        self.repository.fail_ai_run(self.run, code, message, response_id, usage)


@dataclass
class InvestigationRuntimeCore:
    """Provider-neutral application governance for one investigation run."""

    repository: InvestigationRepository
    tools: InvestigationToolRegistry
    incident_id: int
    investigation_id: int | None
    runtime: InvestigationRuntime
    origin: InvestigationOrigin
    max_tool_calls: int
    max_identical_tool_calls: int
    events: InvestigationEventRecorder | None = None
    tracing: TraceBoundary = field(default_factory=TraceBoundary)
    total_tool_calls: int = 0
    repeated_calls: Counter[str] = field(default_factory=Counter)
    selected_tools: list[str] = field(default_factory=list)

    def execute(
        self,
        name: str,
        raw_arguments: str,
        tool_call_id: str | None = None,
        model_turn: int | None = None,
    ) -> GovernedToolCall:
        with self.tracing.span(
            "supportops.tool.execute",
            {
                "supportops.runtime": self.runtime.value,
                "supportops.tool.name": name,
                "supportops.tool.call_id": tool_call_id or "unknown",
                "supportops.model_turn": model_turn or 0,
                "supportops.tool.transport": self.tools.transport,
                **(
                    {"mcp.transport": "stdio"}
                    if self.tools.transport == "mcp"
                    else {}
                ),
            },
        ) as tool_span:
            return self._execute(name, raw_arguments, tool_call_id, model_turn, tool_span)

    def _execute(self, name, raw_arguments, tool_call_id, model_turn, tool_span):
        started = monotonic()
        self._record(
            InvestigationEventType.TOOL_STARTED,
            model_turn=model_turn,
            tool_name=name,
            tool_call_id=tool_call_id,
            status="running",
        )
        self.total_tool_calls += 1
        if self.total_tool_calls > self.max_tool_calls:
            self._record_limit_failure(
                name, tool_call_id, model_turn, started,
                "Tool was not executed because the total call limit was reached",
            )
            raise InvestigationToolLimitError(
                "ai_tool_limit_reached", "Maximum total tool calls reached"
            )

        signature = f"{name}:{raw_arguments}"
        self.repeated_calls[signature] += 1
        if self.repeated_calls[signature] > self.max_identical_tool_calls:
            self._record_limit_failure(
                name, tool_call_id, model_turn, started,
                "Tool was not executed because the identical call limit was reached",
            )
            raise InvestigationToolLimitError(
                "ai_repeated_call_limit", "Repeated identical tool-call limit reached"
            )

        try:
            arguments, result = self.tools.dispatch(name, raw_arguments)
        except Exception:
            self._record(
                InvestigationEventType.TOOL_FAILED,
                model_turn=model_turn,
                tool_name=name,
                tool_call_id=tool_call_id,
                status="failed",
                duration_ms=(monotonic() - started) * 1000,
            )
            raise

        for key, value in self.tracing.safe_resource_attributes(arguments).items():
            tool_span.set_attribute(key, value)
        record_kwargs = {"origin": self.origin, "arguments": arguments}
        if self.investigation_id is not None:
            record_kwargs["investigation_id"] = self.investigation_id
        evidence = self.repository.record_result(self.incident_id, result, **record_kwargs)
        self.selected_tools.append(name)
        tool_status = "completed" if result.success else "failed"
        tool_span.set_attribute("supportops.tool.status", tool_status)
        self._record(
            InvestigationEventType.TOOL_COMPLETED
            if result.success
            else InvestigationEventType.TOOL_FAILED,
            model_turn=model_turn,
            tool_name=name,
            tool_call_id=tool_call_id,
            arguments=arguments,
            result_summary=f"{result.tool} returned {'evidence' if result.success else 'an application error'} for {result.resource}",
            status=tool_status,
            duration_ms=(monotonic() - started) * 1000,
        )
        return GovernedToolCall(
            arguments=arguments,
            result=result,
            evidence_id=evidence.id if evidence is not None else None,
            output=json.dumps(
                {
                    **result.model_dump(mode="json"),
                    "evidence_id": evidence.id if evidence is not None else None,
                }
            ),
        )

    def _record_limit_failure(self, name, tool_call_id, model_turn, started, summary):
        self._record(
            InvestigationEventType.TOOL_FAILED,
            model_turn=model_turn,
            tool_name=name,
            tool_call_id=tool_call_id,
            status="failed",
            duration_ms=(monotonic() - started) * 1000,
            result_summary=summary,
        )

    def _record(self, event_type, **fields: Any) -> None:
        if self.events is not None:
            fields.setdefault("metadata", {"transport": self.tools.transport})
            self.events.record(event_type, **fields)
