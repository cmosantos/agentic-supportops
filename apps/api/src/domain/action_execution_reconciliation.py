from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActionExecutionReconciliationStatus(StrEnum):
    RUNNING = "running"
    DESIRED_STATE_OBSERVED = "desired_state_observed"
    UNDESIRED_STATE_OBSERVED = "undesired_state_observed"
    INCONCLUSIVE = "inconclusive"


class ActionExecutionReconciliationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt_id: int
    execution_id: int
    status: ActionExecutionReconciliationStatus
    observer: str
    expected_outcome: dict[str, Any]
    observed_outcome: dict[str, Any] | None
    evidence: dict[str, Any] | None
    error: dict[str, Any] | None
    requested_at: datetime
    started_at: datetime
    completed_at: datetime | None
