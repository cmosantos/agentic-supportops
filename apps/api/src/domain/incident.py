from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IncidentPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class IncidentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=100)
    priority: IncidentPriority = IncidentPriority.MEDIUM
    requester: str = Field(min_length=1, max_length=200)
    catalog_id: str | None = Field(default=None, max_length=20)
    affected_resource_type: str | None = Field(default=None, max_length=50)
    affected_resource_id: str | None = Field(default=None, max_length=100)
    investigation_context: dict[str, str] = Field(default_factory=dict)


class IncidentRead(IncidentCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: IncidentStatus
    created_at: datetime
    updated_at: datetime
