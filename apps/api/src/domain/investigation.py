from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GoalProfile(StrEnum):
    ROOT_CAUSE = "root_cause"
    ACCOUNT_LOCK_STATE = "account_lock_state"
    APPLICATION_AVAILABILITY = "application_availability"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"


class InvestigationRequest(BaseModel):
    """Client-selectable investigation intent; goal contents remain application-owned."""

    model_config = ConfigDict(extra="forbid")

    goal_profile: GoalProfile | None = None


class InvestigationGoal(BaseModel):
    """Application-owned outcome and boundaries, not an investigation plan."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    constraints: list[str] = Field(min_length=1)
    human_action_required: bool


class InvestigationPlanStep(BaseModel):
    """One immutable statement of intended investigation work."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    intended_action: str = Field(min_length=1)
    purpose: str = Field(min_length=1)


class InvestigationPlan(BaseModel):
    """Application-owned intended path, separate from factual execution history."""

    model_config = ConfigDict(extra="forbid")

    steps: list[InvestigationPlanStep] = Field(min_length=1)

    @model_validator(mode="after")
    def require_contiguous_ordered_sequence(self):
        sequences = [step.sequence for step in self.steps]
        if sequences != list(range(1, len(self.steps) + 1)):
            raise ValueError("plan step sequence must be contiguous and ordered from 1")
        return self


class ToolErrorCode(StrEnum):
    RESOURCE_NOT_FOUND = "resource_not_found"
    USER_NOT_FOUND = "user_not_found"
    MAILBOX_NOT_FOUND = "mailbox_not_found"
    SERVICE_NOT_FOUND = "service_not_found"
    APPLICATION_NOT_FOUND = "application_not_found"
    INVALID_ARGUMENT = "invalid_argument"
    UNKNOWN_TOOL = "unknown_tool"
    MALFORMED_ARGUMENTS = "malformed_arguments"


class InvestigationOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    AI = "ai"
    AGENTS_SDK = "agents_sdk"


class ToolError(BaseModel):
    code: ToolErrorCode
    message: str


class ToolResult(BaseModel):
    tool: str
    resource: str
    success: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None


class InvestigationStepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    investigation_id: int | None
    source: str
    resource: str
    origin: InvestigationOrigin
    payload: dict[str, Any]
    created_at: datetime


class InvestigationStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    investigation_id: int | None
    tool: str
    target_resource: str
    origin: InvestigationOrigin
    arguments: dict[str, Any]
    status: InvestigationStepStatus
    result: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class InvestigationRead(BaseModel):
    incident_id: int
    catalog_id: str | None
    steps: list[InvestigationStepRead]
    evidence: list[EvidenceRead]
