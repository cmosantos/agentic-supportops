from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ResolutionDecision(StrEnum):
    RESOLVE = "resolve"
    KEEP_OPEN = "keep_open"


class IncidentResolutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification_id: int = Field(gt=0)
    decision: ResolutionDecision
    reason: str | None = Field(default=None, max_length=1000)


class IncidentResolutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    verification_id: int
    execution_id: int
    proposal_id: int
    decision: ResolutionDecision
    reason: str | None
    decided_at: datetime
