from datetime import datetime

from pydantic import BaseModel

from domain.ai import InvestigationEventType


class ActionExecutionTimelineEntry(BaseModel):
    timestamp: datetime
    event_type: InvestigationEventType
    execution_id: int
    attempt_id: int | None = None
    status: str | None = None
    description: str
    reason: str | None = None
