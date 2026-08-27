from collections.abc import Generator

from sqlalchemy.orm import Session
from agents import Model
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI

from db.session import SessionLocal
from core.config import settings
from integrations.responses_gateway import ResponsesGateway
from services.tool_registry import InvestigationToolRegistry


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_responses_gateway() -> ResponsesGateway | None:
    if not settings.openai_api_key:
        return None
    tools = InvestigationToolRegistry()
    return ResponsesGateway(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        tools=tools.openai_tools,
        max_retries=settings.openai_max_retries,
        timeout_seconds=settings.openai_timeout_seconds,
        max_output_tokens=settings.ai_max_output_tokens,
    )


def get_agents_sdk_model() -> Model | None:
    if not settings.openai_api_key:
        return None
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        max_retries=settings.openai_max_retries,
        timeout=settings.openai_timeout_seconds,
    )
    return OpenAIResponsesModel(
        model=settings.openai_model,
        openai_client=client,
    )
