from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActionExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    incident_id: int
    capability_name: str
    status: ActionExecutionStatus
    requested_at: datetime
    started_at: datetime
    completed_at: datetime | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
