from collections.abc import Generator

from sqlalchemy.orm import Session

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
    )
