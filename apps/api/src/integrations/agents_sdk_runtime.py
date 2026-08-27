from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from agents import Agent, FunctionTool, Model, ModelSettings
from agents.tool_context import ToolContext

from domain.ai import AIInvestigationResult
from domain.investigation import InvestigationOrigin
from repositories.investigation_repository import InvestigationRepository
from services.tool_registry import InvestigationToolRegistry


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
    total_tool_calls: int = 0
    repeated_calls: Counter[str] = field(default_factory=Counter)
    selected_tools: list[str] = field(default_factory=list)

    def execute(self, name: str, raw_arguments: str) -> str:
        self.total_tool_calls += 1
        if self.total_tool_calls > self.max_tool_calls:
            raise AgentsSDKToolLimitError(
                "ai_tool_limit_reached", "Maximum total tool calls reached"
            )
        signature = f"{name}:{raw_arguments}"
        self.repeated_calls[signature] += 1
        if self.repeated_calls[signature] > self.max_identical_tool_calls:
            raise AgentsSDKToolLimitError(
                "ai_repeated_call_limit", "Repeated identical tool-call limit reached"
            )
        arguments, result = self.tools.dispatch(name, raw_arguments)
        self.repository.record_result(
            self.incident_id,
            result,
            origin=InvestigationOrigin.AGENTS_SDK,
            arguments=arguments,
        )
        self.selected_tools.append(name)
        return result.model_dump_json()


def build_agents_sdk_tools(registry: InvestigationToolRegistry) -> list[FunctionTool]:
    sdk_tools: list[FunctionTool] = []
    for schema in registry.openai_tools:
        name = schema["name"]

        async def invoke(
            context: ToolContext[AgentsSDKRunContext],
            raw_arguments: str,
            tool_name: str = name,
        ) -> str:
            return context.context.execute(tool_name, raw_arguments)

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
