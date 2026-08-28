from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from time import monotonic

from agents import Agent, FunctionTool, Model, ModelSettings
from agents.tool_context import ToolContext

from domain.ai import AIInvestigationResult, InvestigationEventType
from domain.investigation import InvestigationOrigin
from repositories.investigation_repository import InvestigationRepository
from services.tool_registry import InvestigationToolRegistry
from services.investigation_event_recorder import InvestigationEventRecorder
from observability.tracing import TraceBoundary


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "ai-investigator.md"


class AgentsSDKToolLimitError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class AgentsSDKRunContext:
    repository: InvestigationRepository
    tools: InvestigationToolRegistry
    incident_id: int
    max_tool_calls: int
    max_identical_tool_calls: int
    investigation_id: int | None = None
    tracing: TraceBoundary = field(default_factory=TraceBoundary)
    total_tool_calls: int = 0
    repeated_calls: Counter[str] = field(default_factory=Counter)
    selected_tools: list[str] = field(default_factory=list)
    events: InvestigationEventRecorder | None = None
    tool_turns: dict[str, int] = field(default_factory=dict)

    def execute(self, name: str, raw_arguments: str, tool_call_id: str | None) -> str:
        model_turn = self.tool_turns.get(tool_call_id) if tool_call_id else None
        with self.tracing.span(
            "supportops.tool.execute",
            {
                "supportops.runtime": "agents_sdk",
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
            return self._execute(
                name, raw_arguments, tool_call_id, model_turn, tool_span
            )

    def _execute(
        self,
        name: str,
        raw_arguments: str,
        tool_call_id: str | None,
        model_turn: int | None,
        tool_span,
    ) -> str:
        started = monotonic()
        if self.events:
            self.events.record(
                InvestigationEventType.TOOL_STARTED,
                model_turn=model_turn,
                tool_name=name,
                tool_call_id=tool_call_id,
                status="running",
                metadata={"transport": self.tools.transport},
            )
        self.total_tool_calls += 1
        if self.total_tool_calls > self.max_tool_calls:
            if self.events:
                self.events.record(
                    InvestigationEventType.TOOL_FAILED,
                    model_turn=model_turn,
                    tool_name=name,
                    tool_call_id=tool_call_id,
                    status="failed",
                    duration_ms=(monotonic() - started) * 1000,
                    result_summary="Tool was not executed because the total call limit was reached",
                    metadata={"transport": self.tools.transport},
                )
            raise AgentsSDKToolLimitError(
                "ai_tool_limit_reached", "Maximum total tool calls reached"
            )
        signature = f"{name}:{raw_arguments}"
        self.repeated_calls[signature] += 1
        if self.repeated_calls[signature] > self.max_identical_tool_calls:
            if self.events:
                self.events.record(
                    InvestigationEventType.TOOL_FAILED,
                    model_turn=model_turn,
                    tool_name=name,
                    tool_call_id=tool_call_id,
                    status="failed",
                    duration_ms=(monotonic() - started) * 1000,
                    result_summary="Tool was not executed because the identical call limit was reached",
                    metadata={"transport": self.tools.transport},
                )
            raise AgentsSDKToolLimitError(
                "ai_repeated_call_limit", "Repeated identical tool-call limit reached"
            )
        try:
            arguments, result = self.tools.dispatch(name, raw_arguments)
        except Exception:
            if self.events:
                self.events.record(
                    InvestigationEventType.TOOL_FAILED,
                    model_turn=model_turn,
                    tool_name=name,
                    tool_call_id=tool_call_id,
                    status="failed",
                    duration_ms=(monotonic() - started) * 1000,
                    metadata={"transport": self.tools.transport},
                )
            raise
        for key, value in self.tracing.safe_resource_attributes(arguments).items():
            tool_span.set_attribute(key, value)
        record_arguments = {
            "origin": InvestigationOrigin.AGENTS_SDK,
            "arguments": arguments,
        }
        if self.investigation_id is not None:
            record_arguments["investigation_id"] = self.investigation_id
        evidence = self.repository.record_result(
            self.incident_id, result, **record_arguments
        )
        self.selected_tools.append(name)
        tool_span.set_attribute(
            "supportops.tool.status", "completed" if result.success else "failed"
        )
        if self.events:
            self.events.record(
                InvestigationEventType.TOOL_COMPLETED if result.success else InvestigationEventType.TOOL_FAILED,
                model_turn=model_turn,
                tool_name=name,
                tool_call_id=tool_call_id,
                arguments=arguments,
                result_summary=f"{result.tool} returned {'evidence' if result.success else 'an application error'} for {result.resource}",
                status="completed" if result.success else "failed",
                duration_ms=(monotonic() - started) * 1000,
                metadata={"transport": self.tools.transport},
            )
        return json.dumps(
            {
                **result.model_dump(mode="json"),
                "evidence_id": evidence.id if evidence else None,
            }
        )


class ObservableAgentsModel(Model):
    """Observes the injected SDK model boundary without owning the Runner loop."""

    def __init__(
        self,
        model: Model,
        model_name: str,
        events: InvestigationEventRecorder,
        context: AgentsSDKRunContext,
    ) -> None:
        self._model = model
        self._model_name = model_name
        self._events = events
        self._context = context
        self._turn = 0

    async def get_response(self, *args, **kwargs):
        self._turn += 1
        turn = self._turn
        with self._context.tracing.span(
            "supportops.model.turn",
            {
                "supportops.runtime": "agents_sdk",
                "supportops.model": self._model_name,
                "supportops.model_turn": turn,
            },
        ) as model_span:
            self._events.record(
                InvestigationEventType.MODEL_TURN_STARTED,
                model_turn=turn,
                status="running",
            )
            started = monotonic()
            try:
                response = await self._model.get_response(*args, **kwargs)
            except Exception:
                self._events.record(
                    InvestigationEventType.MODEL_TURN_COMPLETED,
                    model_turn=turn,
                    status="failed",
                    duration_ms=(monotonic() - started) * 1000,
                )
                raise
            for item in response.output:
                if getattr(item, "type", None) != "function_call":
                    continue
                call_id = getattr(item, "call_id", None)
                if call_id:
                    self._context.tool_turns[call_id] = turn
                try:
                    arguments = json.loads(item.arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = None
                self._events.record(
                    InvestigationEventType.TOOL_REQUESTED,
                    model_turn=turn,
                    tool_name=item.name,
                    tool_call_id=call_id,
                    arguments=arguments,
                    status="requested",
                )
            usage = response.usage
            model_span.set_attribute(
                "supportops.response_id", response.response_id or "unknown"
            )
            model_span.set_attribute("supportops.input_tokens", usage.input_tokens)
            model_span.set_attribute("supportops.output_tokens", usage.output_tokens)
            model_span.set_attribute("supportops.total_tokens", usage.total_tokens)
            self._events.record(
                InvestigationEventType.MODEL_TURN_COMPLETED,
                model_turn=turn,
                response_id=response.response_id,
                model=self._model_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                status="completed",
                duration_ms=(monotonic() - started) * 1000,
            )
            return response

    def stream_response(self, *args, **kwargs):
        return self._model.stream_response(*args, **kwargs)


def build_agents_sdk_tools(registry: InvestigationToolRegistry) -> list[FunctionTool]:
    sdk_tools: list[FunctionTool] = []
    for schema in registry.openai_tools:
        name = schema["name"]

        async def invoke(
            context: ToolContext[AgentsSDKRunContext],
            raw_arguments: str,
            tool_name: str = name,
        ) -> str:
            return context.context.execute(
                tool_name, raw_arguments, getattr(context, "tool_call_id", None)
            )

        sdk_tools.append(
            FunctionTool(
                name=name,
                description=schema["description"],
                params_json_schema=schema["parameters"],
                on_invoke_tool=invoke,
                strict_json_schema=True,
            )
        )
    return sdk_tools


def build_supportops_agent(
    model: Model,
    registry: InvestigationToolRegistry,
    max_output_tokens: int,
    timeout_seconds: float,
) -> Agent[AgentsSDKRunContext]:
    return Agent[AgentsSDKRunContext](
        name="SupportOps Investigator",
        instructions=PROMPT_PATH.read_text(encoding="utf-8"),
        model=model,
        model_settings=ModelSettings(
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            retry=None,
            parallel_tool_calls=True,
        ),
        tools=build_agents_sdk_tools(registry),
        output_type=AIInvestigationResult,
    )
