from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    source: str
    resource: str
    origin: InvestigationOrigin
    payload: dict[str, Any]
    created_at: datetime


class InvestigationStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
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
