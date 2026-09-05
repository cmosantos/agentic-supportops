from dataclasses import dataclass, field
import json
from pathlib import Path
from time import monotonic

from agents import Agent, FunctionTool, Model, ModelSettings
from agents.tool_context import ToolContext

from domain.ai import AIInvestigationResult, InvestigationEventType, InvestigationRuntime
from domain.investigation import InvestigationOrigin
from repositories.investigation_repository import InvestigationRepository
from services.tool_registry import InvestigationToolRegistry
from services.investigation_event_recorder import InvestigationEventRecorder
from observability.tracing import TraceBoundary
from services.investigation_runtime_core import (
    InvestigationRuntimeCore,
    InvestigationToolLimitError,
)


PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"
ORCHESTRATOR_PROMPT_PATH = PROMPT_ROOT / "supportops-orchestrator.md"
SPECIALIST_PROMPT_PATHS = {
    "identity_access": PROMPT_ROOT / "identity-access-specialist.md",
    "endpoint_network": PROMPT_ROOT / "endpoint-network-specialist.md",
    "infrastructure_application": (
        PROMPT_ROOT / "infrastructure-application-specialist.md"
    ),
}

SPECIALIST_TOOL_ALLOWLISTS = {
    "identity_access": (
        "get_user",
        "get_account_status",
        "get_user_groups",
        "get_user_licenses",
        "get_mailbox",
        "get_mailbox_permissions",
    ),
    "endpoint_network": (
        "get_device",
        "get_cpu_usage",
        "get_memory_usage",
        "get_disk_usage",
        "get_network_config",
        "get_service_status",
        "check_gateway_connectivity",
        "check_external_connectivity",
        "check_dns_resolution",
    ),
    "infrastructure_application": (
        "get_host_status",
        "get_recent_alerts",
        "get_metrics",
        "get_service_health",
        "get_application_health",
    ),
}


class AgentsSDKToolLimitError(InvestigationToolLimitError):
    pass


@dataclass
class AgentsSDKRunContext:
    repository: InvestigationRepository
    tools: InvestigationToolRegistry
    incident_id: int
    max_tool_calls: int
    max_identical_tool_calls: int
    investigation_id: int | None = None
    tracing: TraceBoundary = field(default_factory=TraceBoundary)
    events: InvestigationEventRecorder | None = None
    tool_turns: dict[str, int] = field(default_factory=dict)
    terminal_tool_error: AgentsSDKToolLimitError | None = None
    governance: InvestigationRuntimeCore = field(init=False)

    def __post_init__(self) -> None:
        self.governance = InvestigationRuntimeCore(
            repository=self.repository,
            tools=self.tools,
            incident_id=self.incident_id,
            investigation_id=self.investigation_id,
            runtime=InvestigationRuntime.AGENTS_SDK,
            origin=InvestigationOrigin.AGENTS_SDK,
            max_tool_calls=self.max_tool_calls,
            max_identical_tool_calls=self.max_identical_tool_calls,
            events=self.events,
            tracing=self.tracing,
        )

    def execute(self, name: str, raw_arguments: str, tool_call_id: str | None) -> str:
        model_turn = self.tool_turns.get(tool_call_id) if tool_call_id else None
        try:
            return self.governance.execute(
                name, raw_arguments, tool_call_id, model_turn
            ).output
        except InvestigationToolLimitError as error:
            sdk_error = AgentsSDKToolLimitError(error.code, str(error))
            self.terminal_tool_error = sdk_error
            raise sdk_error from error


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


def build_agents_sdk_tools(
    registry: InvestigationToolRegistry,
    allowed_names: tuple[str, ...] | None = None,
) -> list[FunctionTool]:
    allowed = set(allowed_names) if allowed_names is not None else None
    sdk_tools: list[FunctionTool] = []
    for schema in registry.openai_tools:
        name = schema["name"]
        if allowed is not None and name not in allowed:
            continue

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


def build_supportops_specialists(
    model: Model,
    registry: InvestigationToolRegistry,
    max_output_tokens: int,
    timeout_seconds: float,
) -> list[Agent[AgentsSDKRunContext]]:
    settings = ModelSettings(
        max_tokens=max_output_tokens,
        timeout=timeout_seconds,
        retry=None,
        parallel_tool_calls=True,
    )
    definitions = (
        ("Identity & Access Specialist", "identity_access"),
        ("Endpoint & Network Specialist", "endpoint_network"),
        (
            "Infrastructure & Application Specialist",
            "infrastructure_application",
        ),
    )
    return [
        Agent[AgentsSDKRunContext](
            name=name,
            instructions=SPECIALIST_PROMPT_PATHS[key].read_text(encoding="utf-8"),
            model=model,
            model_settings=settings,
            tools=build_agents_sdk_tools(registry, SPECIALIST_TOOL_ALLOWLISTS[key]),
        )
        for name, key in definitions
    ]


def _as_audited_specialist_tool(
    specialist: Agent[AgentsSDKRunContext],
    tool_name: str,
    tool_description: str,
) -> FunctionTool:
    specialist_tool = specialist.as_tool(
        tool_name=tool_name,
        tool_description=tool_description,
    )
    invoke_specialist = specialist_tool.on_invoke_tool

    async def invoke(
        context: ToolContext[AgentsSDKRunContext], raw_arguments: str
    ) -> str:
        tool_call_id = getattr(context, "tool_call_id", None)
        model_turn = (
            context.context.tool_turns.get(tool_call_id) if tool_call_id else None
        )
        started = monotonic()
        metadata = {"kind": "agent_delegation", "agent": specialist.name}
        if context.context.events:
            context.context.events.record(
                InvestigationEventType.TOOL_STARTED,
                model_turn=model_turn,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="running",
                metadata=metadata,
            )
        try:
            output = await invoke_specialist(context, raw_arguments)
            if context.context.terminal_tool_error is not None:
                raise context.context.terminal_tool_error
        except Exception:
            if context.context.events:
                context.context.events.record(
                    InvestigationEventType.TOOL_FAILED,
                    model_turn=model_turn,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status="failed",
                    duration_ms=(monotonic() - started) * 1000,
                    metadata=metadata,
                )
            raise
        if context.context.events:
            context.context.events.record(
                InvestigationEventType.TOOL_COMPLETED,
                model_turn=model_turn,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                status="completed",
                duration_ms=(monotonic() - started) * 1000,
                metadata=metadata,
            )
        return output

    specialist_tool.on_invoke_tool = invoke
    return specialist_tool


def build_supportops_agent(
    model: Model,
    registry: InvestigationToolRegistry,
    max_output_tokens: int,
    timeout_seconds: float,
) -> Agent[AgentsSDKRunContext]:
    specialists = build_supportops_specialists(
        model, registry, max_output_tokens, timeout_seconds
    )
    specialist_tools = [
        _as_audited_specialist_tool(
            specialists[0],
            "investigate_identity_access",
            "Investigate identity, access, licensing, mailbox, or mailbox-permission evidence.",
        ),
        _as_audited_specialist_tool(
            specialists[1],
            "investigate_endpoint_network",
            "Investigate endpoint resource, service, network, gateway, external connectivity, or DNS evidence.",
        ),
        _as_audited_specialist_tool(
            specialists[2],
            "investigate_infrastructure_application",
            "Investigate host, alert, metric, service-health, or application-health evidence.",
        ),
    ]
    return Agent[AgentsSDKRunContext](
        name="SupportOps Orchestrator",
        instructions=ORCHESTRATOR_PROMPT_PATH.read_text(encoding="utf-8"),
        model=model,
        model_settings=ModelSettings(
            max_tokens=max_output_tokens,
            timeout=timeout_seconds,
            retry=None,
            parallel_tool_calls=True,
        ),
        tools=specialist_tools,
        output_type=AIInvestigationResult,
    )
