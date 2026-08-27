from pathlib import Path
from typing import Any

import openai
from openai import OpenAI

from domain.ai import (
    AIInvestigationResult,
    FunctionCallOutput,
    ProviderFunctionCall,
    ProviderUsage,
    ResponsesTurn,
)


PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "ai-investigator.md"


class ResponsesProviderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ResponsesGateway:
    def __init__(
        self,
        api_key: str,
        model: str,
        tools: list[dict[str, Any]],
        max_retries: int,
        timeout_seconds: float,
        max_output_tokens: int,
        client: OpenAI | None = None,
    ) -> None:
        self.model = model
        self._tools = tools
        self._client = client or OpenAI(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout_seconds,
        )
        self._max_output_tokens = max_output_tokens
        self._instructions = PROMPT_PATH.read_text(encoding="utf-8")
        self._text_format = {
            "format": {
                "type": "json_schema",
                "name": "ai_investigation_result",
                "strict": True,
                "schema": AIInvestigationResult.model_json_schema(),
            }
        }

    def create_initial(self, incident_input: str) -> ResponsesTurn:
        return self._call(input=incident_input)

    def continue_with_outputs(
        self, previous_response_id: str, outputs: list[FunctionCallOutput]
    ) -> ResponsesTurn:
        provider_outputs = [
            {
                "type": "function_call_output",
                "call_id": item.call_id,
                "output": item.output,
            }
            for item in outputs
        ]
        return self._call(
            input=provider_outputs,
            previous_response_id=previous_response_id,
        )

    def _call(self, **arguments: Any) -> ResponsesTurn:
        try:
            response = self._client.responses.create(
                model=self.model,
                instructions=self._instructions,
                tools=self._tools,
                text=self._text_format,
                max_output_tokens=self._max_output_tokens,
                parallel_tool_calls=True,
                store=True,
                **arguments,
            )
        except openai.AuthenticationError as error:
            raise ResponsesProviderError("ai_authentication_failed", "OpenAI authentication failed") from error
        except openai.RateLimitError as error:
            raise ResponsesProviderError("ai_rate_limited", "OpenAI rate limit reached") from error
        except openai.APITimeoutError as error:
            raise ResponsesProviderError("ai_timeout", "OpenAI request timed out") from error
        except (openai.APIConnectionError, openai.APIError) as error:
            raise ResponsesProviderError("ai_provider_unavailable", "OpenAI API is unavailable") from error
        calls = [
            ProviderFunctionCall(
                call_id=item.call_id,
                name=item.name,
                arguments=item.arguments,
            )
            for item in response.output
            if item.type == "function_call"
        ]
        usage = response.usage
        return ResponsesTurn(
            response_id=response.id,
            model=response.model,
            function_calls=calls,
            output_text=response.output_text,
            usage=ProviderUsage(
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                total_tokens=getattr(usage, "total_tokens", 0) or 0,
            ),
        )
