from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ActionExecutionReconciliationStatus(StrEnum):
    RUNNING = "running"
    DESIRED_STATE_OBSERVED = "desired_state_observed"
    UNDESIRED_STATE_OBSERVED = "undesired_state_observed"
    INCONCLUSIVE = "inconclusive"


class ReconciliationRecoveryBlockReason(StrEnum):
    RECONCILIATION_NOT_RUNNING = 'reconciliation_not_running'
    NOT_STALE = 'not_stale'
    ATTEMPT_NOT_CANONICAL = 'attempt_not_canonical'
    INVOCATION_NOT_STARTED = 'invocation_not_started'
    EXECUTION_NOT_OUTCOME_UNKNOWN = 'execution_not_outcome_unknown'
    ATTEMPT_NOT_OUTCOME_UNKNOWN = 'attempt_not_outcome_unknown'
    OUTCOME_CERTAINTY_NOT_UNKNOWN = 'outcome_certainty_not_unknown'
    PROPOSAL_UNAVAILABLE = 'proposal_unavailable'
    POLICY_UNAVAILABLE = 'policy_unavailable'
    POLICY_MISMATCH = 'policy_mismatch'


def reconciliation_recovery_block_reason(
    *,
    reconciliation_running: bool,
    is_stale: bool,
    canonical_attempt: bool,
    invocation_started: bool,
    execution_outcome_unknown: bool,
    attempt_outcome_unknown: bool,
    outcome_certainty_unknown: bool,
    proposal_available: bool,
    policy_available: bool,
    policy_matches: bool,
) -> ReconciliationRecoveryBlockReason | None:
    checks = (
        (reconciliation_running, ReconciliationRecoveryBlockReason.RECONCILIATION_NOT_RUNNING),
        (is_stale, ReconciliationRecoveryBlockReason.NOT_STALE),
        (canonical_attempt, ReconciliationRecoveryBlockReason.ATTEMPT_NOT_CANONICAL),
        (invocation_started, ReconciliationRecoveryBlockReason.INVOCATION_NOT_STARTED),
        (execution_outcome_unknown, ReconciliationRecoveryBlockReason.EXECUTION_NOT_OUTCOME_UNKNOWN),
        (attempt_outcome_unknown, ReconciliationRecoveryBlockReason.ATTEMPT_NOT_OUTCOME_UNKNOWN),
        (outcome_certainty_unknown, ReconciliationRecoveryBlockReason.OUTCOME_CERTAINTY_NOT_UNKNOWN),
        (proposal_available, ReconciliationRecoveryBlockReason.PROPOSAL_UNAVAILABLE),
        (policy_available, ReconciliationRecoveryBlockReason.POLICY_UNAVAILABLE),
        (policy_matches, ReconciliationRecoveryBlockReason.POLICY_MISMATCH),
    )
    return next((reason for allowed, reason in checks if not allowed), None)


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


class ActionExecutionReconciliationOperationalRead(
    ActionExecutionReconciliationRead
):
    is_stale: bool
    recoverable: bool
    recovery_block_reason: ReconciliationRecoveryBlockReason | None
