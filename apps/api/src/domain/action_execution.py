from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActionExecutionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class ActionExecutionAttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


class FailureCause(StrEnum):
    TOOL_REJECTED = "tool_rejected"
    TOOL_EXCEPTION = "tool_exception"
    TIMEOUT = "timeout"
    PROCESS_INTERRUPTED = "process_interrupted"
    RESULT_INVALID = "result_invalid"
    TERMINAL_PERSISTENCE_FAILED = "terminal_persistence_failed"
    LEGACY_UNCLASSIFIED = "legacy_unclassified"


class OutcomeCertainty(StrEnum):
    APPLIED_ACKNOWLEDGED = "applied_acknowledged"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"
    LEGACY_UNDETERMINED = "legacy_undetermined"


class ActionExecutionCompletionBasis(StrEnum):
    ACKNOWLEDGED_RESULT = "acknowledged_result"
    RECONCILIATION = "reconciliation"
    LEGACY_RECORDED = "legacy_recorded"


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
    completion_basis: ActionExecutionCompletionBasis | None = None


class ActionExecutionAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    execution_id: int
    attempt_number: int
    status: ActionExecutionAttemptStatus
    claimed_at: datetime
    invocation_started_at: datetime | None
    completed_at: datetime | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    failure_cause: FailureCause | None
    outcome_certainty: OutcomeCertainty | None
    created_at: datetime
