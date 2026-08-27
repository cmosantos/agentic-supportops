import json

import openai
from agents import Model, RunConfig, Runner
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, ModelTimeoutError

from db.models import IncidentRecord
from domain.ai import AIInvestigationExecution, AIInvestigationRead, AIInvestigationResult, ProviderUsage
from domain.investigation import EvidenceRead, InvestigationOrigin, InvestigationStepRead
from integrations.agents_sdk_runtime import (
    AgentsSDKRunContext,
    AgentsSDKToolLimitError,
    build_supportops_agent,
)
from repositories.investigation_repository import InvestigationRepository
from services.ai_investigation_service import AIInvestigationError
from services.tool_registry import InvestigationToolRegistry


AGENTS_SDK_MODE = "agents_sdk"


class AgentsSDKInvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        tools: InvestigationToolRegistry,
        model: Model | None,
        model_name: str,
        max_turns: int,
        max_tool_calls: int,
        max_identical_tool_calls: int,
        max_output_tokens: int,
        timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._tools = tools
        self._model = model
        self._model_name = model_name
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls
        self._max_identical_tool_calls = max_identical_tool_calls
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    def investigate(self, incident: IncidentRecord) -> AIInvestigationExecution:
        if self._model is None:
            raise AIInvestigationError("ai_not_configured", "OpenAI is not configured")
        self._repository.replace_start(incident.id, InvestigationOrigin.AGENTS_SDK)
        run = self._repository.start_ai_run(
            incident.id, self._model_name, mode=AGENTS_SDK_MODE
        )
        usage = ProviderUsage(runtime=AGENTS_SDK_MODE)
        last_response_id: str | None = None
        context = AgentsSDKRunContext(
            repository=self._repository,
            tools=self._tools,
            incident_id=incident.id,
            max_tool_calls=self._max_tool_calls,
            max_identical_tool_calls=self._max_identical_tool_calls,
        )
        agent = build_supportops_agent(
            self._model,
            self._tools,
            self._max_output_tokens,
            self._timeout_seconds,
        )
        try:
            result = Runner.run_sync(
                agent,
                self._incident_input(incident),
                context=context,
                max_turns=self._max_turns,
                run_config=RunConfig(tracing_disabled=True),
            )
            last_response_id = result.last_response_id
            native_usage = result.context_wrapper.usage
            usage = ProviderUsage(
                input_tokens=native_usage.input_tokens,
                output_tokens=native_usage.output_tokens,
                total_tokens=native_usage.total_tokens,
                response_iterations=native_usage.requests,
                requests=native_usage.requests,
                runtime=AGENTS_SDK_MODE,
                final_agent=result.last_agent.name,
            )
            if not isinstance(result.final_output, AIInvestigationResult):
                raise AIInvestigationError(
                    "ai_invalid_result", "Agents SDK returned an invalid investigation result"
                )
            completed = self._repository.complete_ai_run(
                run, result.final_output, last_response_id, usage
            )
            return self._execution(incident.id, completed)
        except MaxTurnsExceeded as error:
            usage, last_response_id = self._error_metadata(error, usage, last_response_id)
            self._fail(run, "ai_loop_limit_reached", "Maximum Agents SDK turns reached", last_response_id, usage)
            raise AIInvestigationError(
                "ai_loop_limit_reached", "Maximum Agents SDK turns reached"
            ) from error
        except ModelTimeoutError as error:
            usage, last_response_id = self._error_metadata(error, usage, last_response_id)
            self._fail(run, "ai_timeout", "OpenAI request timed out", last_response_id, usage)
            raise AIInvestigationError("ai_timeout", "OpenAI request timed out") from error
        except ModelBehaviorError as error:
            usage, last_response_id = self._error_metadata(error, usage, last_response_id)
            self._fail(run, "ai_invalid_result", "Agents SDK returned invalid model output", last_response_id, usage)
            raise AIInvestigationError(
                "ai_invalid_result", "Agents SDK returned invalid model output"
            ) from error
        except AgentsSDKToolLimitError as error:
            self._fail(run, error.code, str(error), last_response_id, usage)
            raise AIInvestigationError(error.code, str(error)) from error
        except openai.AuthenticationError as error:
            self._fail(run, "ai_authentication_failed", "OpenAI authentication failed", last_response_id, usage)
            raise AIInvestigationError(
                "ai_authentication_failed", "OpenAI authentication failed"
            ) from error
        except openai.RateLimitError as error:
            self._fail(run, "ai_rate_limited", "OpenAI rate limit reached", last_response_id, usage)
            raise AIInvestigationError("ai_rate_limited", "OpenAI rate limit reached") from error
        except (openai.APIConnectionError, openai.APIError) as error:
            self._fail(run, "ai_provider_unavailable", "OpenAI API is unavailable", last_response_id, usage)
            raise AIInvestigationError(
                "ai_provider_unavailable", "OpenAI API is unavailable"
            ) from error
        except AIInvestigationError as error:
            self._fail(run, error.code, str(error), last_response_id, usage)
            raise
        except Exception as error:
            limit_error = self._find_tool_limit_error(error)
            if limit_error is not None:
                self._fail(
                    run,
                    limit_error.code,
                    str(limit_error),
                    last_response_id,
                    usage,
                )
                raise AIInvestigationError(
                    limit_error.code, str(limit_error)
                ) from error
            self._fail(run, "agents_sdk_failure", "Agents SDK investigation failed", last_response_id, usage)
            raise AIInvestigationError(
                "agents_sdk_failure", "Agents SDK investigation failed"
            ) from error

    def get_latest(self, incident_id: int) -> AIInvestigationExecution | None:
        record = self._repository.get_ai_run(incident_id, mode=AGENTS_SDK_MODE)
        return self._execution(incident_id, record) if record else None

    def _execution(self, incident_id: int, record) -> AIInvestigationExecution:
        return AIInvestigationExecution(
            investigation=AIInvestigationRead.model_validate(record),
            evidence=[
                EvidenceRead.model_validate(item)
                for item in self._repository.list_evidence(
                    incident_id, InvestigationOrigin.AGENTS_SDK
                )
            ],
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(
                    incident_id, InvestigationOrigin.AGENTS_SDK
                )
            ],
        )

    def _fail(self, run, code: str, message: str, response_id, usage) -> None:
        self._repository.fail_ai_run(run, code, message, response_id, usage)

    @staticmethod
    def _error_metadata(error, fallback_usage, fallback_response_id):
        run_data = getattr(error, "run_data", None)
        raw_responses = getattr(run_data, "raw_responses", None) or []
        if not raw_responses:
            return fallback_usage, fallback_response_id
        return (
            ProviderUsage(
                input_tokens=sum(item.usage.input_tokens for item in raw_responses),
                output_tokens=sum(item.usage.output_tokens for item in raw_responses),
                total_tokens=sum(item.usage.total_tokens for item in raw_responses),
                response_iterations=sum(item.usage.requests for item in raw_responses),
                requests=sum(item.usage.requests for item in raw_responses),
                runtime=AGENTS_SDK_MODE,
                final_agent=getattr(getattr(run_data, "last_agent", None), "name", None),
            ),
            raw_responses[-1].response_id or fallback_response_id,
        )

    @classmethod
    def _find_tool_limit_error(cls, error) -> AgentsSDKToolLimitError | None:
        if isinstance(error, AgentsSDKToolLimitError):
            return error
        if isinstance(error, BaseExceptionGroup):
            for nested in error.exceptions:
                match = cls._find_tool_limit_error(nested)
                if match is not None:
                    return match
        cause = error.__cause__ or error.__context__
        if cause is not None and cause is not error:
            return cls._find_tool_limit_error(cause)
        return None

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
