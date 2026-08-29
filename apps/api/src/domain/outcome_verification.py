from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class OutcomeVerificationStatus(StrEnum):
    RUNNING = "running"
    VERIFIED = "verified"
    NOT_VERIFIED = "not_verified"
    FAILED = "failed"


class OutcomeVerificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    execution_id: int
    proposal_id: int
    incident_id: int
    status: OutcomeVerificationStatus
    requested_at: datetime
    started_at: datetime
    completed_at: datetime | None
    expected_outcome: dict[str, Any]
    observed_outcome: dict[str, Any] | None
    evidence: dict[str, Any] | None
    error: dict[str, str] | None
