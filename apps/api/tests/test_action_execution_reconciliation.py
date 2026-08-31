from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from db.models import ActionExecutionAttemptRecord, ActionExecutionRecord
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.investigation import ToolResult
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
)
from services.action_execution_reconciliation_service import (
    ActionExecutionReconciliationError,
    ActionExecutionReconciliationService,
)
from services.verification_policy import (
    VerificationPolicy,
    VerificationPolicyDeniedError,
)

from tests.test_action_execution_recovery import (
    OLD,
    recovery_sessions,
    seed_recovery_context,
)


class RecordingObserver:
    def __init__(self, data: dict | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self._data = data if data is not None else {"status": "healthy"}

    def execute(self, name: str, arguments: dict[str, str]) -> ToolResult:
        self.calls.append((name, arguments))
        return ToolResult(
            tool=name,
            resource=arguments["application_id"],
            success=True,
            data=self._data,
        )


def seed_unknown(sessions) -> tuple[int, int, int]:
    return seed_recovery_context(
        sessions,
        invocation_started_at=OLD,
        execution_status=ActionExecutionStatus.OUTCOME_UNKNOWN,
        attempt_status=ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
        failure_cause=FailureCause.TOOL_EXCEPTION,
        outcome_certainty=OutcomeCertainty.UNKNOWN,
    )


def reconcile(sessions, execution_id: int, attempt_id: int, tools=None):
    with sessions() as session:
        return ActionExecutionReconciliationService(
            ActionExecutionReconciliationRepository(session),
            VerificationPolicy(),
            tools or RecordingObserver(),
        ).reconcile(execution_id, attempt_id)


def assert_error(code: str, operation) -> None:
    with pytest.raises(ActionExecutionReconciliationError) as captured:
        operation()
    assert captured.value.code == code


def test_service_calls_only_governed_observer_and_never_creates_attempt_two(
    recovery_sessions,
) -> None:
    execution_id, attempt_id, _ = seed_unknown(recovery_sessions)
    tools = RecordingObserver()

    result = reconcile(recovery_sessions, execution_id, attempt_id, tools)
    with recovery_sessions() as session:
        attempts = list(
            session.scalars(
                select(ActionExecutionAttemptRecord).where(
                    ActionExecutionAttemptRecord.execution_id == execution_id
                )
            )
        )

    assert result.status.value == "desired_state_observed"
    assert tools.calls == [
        ("get_application_health", {"application_id": "SUPPORT-API"})
    ]
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN


def test_unclassifiable_successful_observation_is_inconclusive(
    recovery_sessions,
) -> None:
    execution_id, attempt_id, _ = seed_unknown(recovery_sessions)

    result = reconcile(
        recovery_sessions,
        execution_id,
        attempt_id,
        RecordingObserver({"latency_ms": 12}),
    )

    assert result.status.value == "inconclusive"
    assert result.observed_outcome is None
    with recovery_sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
        assert attempt.status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN


def test_missing_and_mismatched_attempts_are_rejected(recovery_sessions) -> None:
    execution_id, _, _ = seed_unknown(recovery_sessions)
    _, other_attempt_id, _ = seed_unknown(recovery_sessions)

    assert_error(
        "action_execution_not_found",
        lambda: reconcile(recovery_sessions, 999999, other_attempt_id),
    )
    assert_error(
        "execution_attempt_not_found",
        lambda: reconcile(recovery_sessions, execution_id, 999999),
    )
    assert_error(
        "execution_attempt_mismatch",
        lambda: reconcile(recovery_sessions, execution_id, other_attempt_id),
    )


def test_attempt_two_is_not_canonical(recovery_sessions) -> None:
    execution_id, _, _ = seed_unknown(recovery_sessions)
    with recovery_sessions() as session:
        attempt = ActionExecutionAttemptRecord(
            execution_id=execution_id,
            attempt_number=2,
            status=ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            claimed_at=datetime.now(timezone.utc),
            invocation_started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc),
            error={"code": "legacy_test"},
            failure_cause=FailureCause.LEGACY_UNCLASSIFIED,
            outcome_certainty=OutcomeCertainty.UNKNOWN,
        )
        session.add(attempt)
        session.commit()
        attempt_id = attempt.id

    assert_error(
        "execution_attempt_not_canonical",
        lambda: reconcile(recovery_sessions, execution_id, attempt_id),
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "execution_status",
            ActionExecutionStatus.FAILED,
            "execution_not_outcome_unknown",
        ),
        (
            "attempt_status",
            ActionExecutionAttemptStatus.FAILED,
            "execution_attempt_not_outcome_unknown",
        ),
        (
            "certainty",
            OutcomeCertainty.NOT_APPLIED,
            "execution_attempt_certainty_not_unknown",
        ),
    ],
)
def test_noneligible_unknown_contract_is_rejected(
    recovery_sessions, field, value, code
) -> None:
    execution_id, attempt_id, _ = seed_unknown(recovery_sessions)
    with recovery_sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        if field == "execution_status":
            execution.status = value
        elif field == "attempt_status":
            attempt.status = value
        else:
            attempt.outcome_certainty = value
        session.commit()

    assert_error(code, lambda: reconcile(recovery_sessions, execution_id, attempt_id))


def test_missing_policy_is_denied_before_observation(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_unknown(recovery_sessions)
    tools = RecordingObserver()
    with recovery_sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        execution.capability_name = "unsupported_capability"
        session.commit()

    with pytest.raises(VerificationPolicyDeniedError):
        reconcile(recovery_sessions, execution_id, attempt_id, tools)
    assert tools.calls == []
