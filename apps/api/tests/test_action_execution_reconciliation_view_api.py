from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from api.dependencies import get_controlled_tools
from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionReconciliationRecord,
    ActionExecutionRecord,
    InvestigationEventRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.action_execution_reconciliation import (
    ActionExecutionReconciliationStatus,
)
from main import app
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
)
from services.action_execution_reconciliation_service import (
    ActionExecutionReconciliationService,
)
from tools.actions import ActionTools
from tools.monitoring import MonitoringTools

from tests.test_action_execution import execution_context
from tests.test_action_execution_reconciliation_api import (
    create_unknown_execution,
)
from tests.test_action_execution_reconciliation_recovery_api import (
    make_running_reconciliation,
)
from tests.test_action_execution_recovery import OLD, seed_recovery_context


NOW = datetime(2026, 8, 31, 15, tzinfo=timezone.utc)


def view_url(execution_id: int, attempt_id: int) -> str:
    return (
        f"/action-executions/{execution_id}/attempts/{attempt_id}"
        "/reconciliation"
    )


def set_reconciliation_started_at(sessions, started_at: datetime) -> None:
    with sessions() as session:
        reconciliation = session.scalar(
            select(ActionExecutionReconciliationRecord)
        )
        reconciliation.started_at = started_at
        session.commit()


def persisted_snapshot(sessions, execution_id: int) -> dict:
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempts = list(
            session.scalars(
                select(ActionExecutionAttemptRecord).where(
                    ActionExecutionAttemptRecord.execution_id == execution_id
                )
            )
        )
        reconciliations = list(
            session.scalars(
                select(ActionExecutionReconciliationRecord).where(
                    ActionExecutionReconciliationRecord.execution_id == execution_id
                )
            )
        )
        events = list(session.scalars(select(InvestigationEventRecord)))
        return {
            "execution": (
                execution.status,
                execution.completed_at,
                execution.completion_basis,
                execution.result,
                execution.error,
            ),
            "attempts": [
                (
                    item.id,
                    item.status,
                    item.outcome_certainty,
                    item.completed_at,
                )
                for item in attempts
            ],
            "reconciliations": [
                (
                    item.id,
                    item.status,
                    item.started_at,
                    item.completed_at,
                    item.observed_outcome,
                    item.error,
                )
                for item in reconciliations
            ],
            "event_ids": [item.id for item in events],
        }


@pytest.mark.parametrize(
    ("age_seconds", "is_stale", "recoverable", "reason"),
    [
        (299, False, False, 'not_stale'),
        (300, True, True, None),
        (301, True, True, None),
    ],
)
def test_running_view_uses_exact_stale_boundary_without_mutation(
    execution_context,
    monkeypatch: pytest.MonkeyPatch,
    age_seconds: int,
    is_stale: bool,
    recoverable: bool,
    reason: str | None,
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False, stale=False
    )
    set_reconciliation_started_at(
        sessions, NOW - timedelta(seconds=age_seconds)
    )
    monkeypatch.setattr(
        ActionExecutionReconciliationService,
        "_utc_now",
        staticmethod(lambda: NOW),
    )
    before = persisted_snapshot(sessions, execution_id)

    response = client.get(view_url(execution_id, attempt_id))
    after = persisted_snapshot(sessions, execution_id)

    assert response.status_code == 200
    assert response.json()["status"] == "running"
    assert response.json()["is_stale"] is is_stale
    assert response.json()["recoverable"] is recoverable
    assert response.json()["recovery_block_reason"] == reason
    assert after == before


def test_stale_running_view_is_not_recoverable_when_execution_is_ineligible(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        execution.status = ActionExecutionStatus.FAILED
        session.commit()

    response = client.get(view_url(execution_id, attempt_id))

    assert response.status_code == 200
    assert response.json()["is_stale"] is True
    assert response.json()["recoverable"] is False
    assert response.json()["recovery_block_reason"] == (
        "execution_not_outcome_unknown"
    )


@pytest.mark.parametrize(
    "terminal_status",
    [
        ActionExecutionReconciliationStatus.DESIRED_STATE_OBSERVED,
        ActionExecutionReconciliationStatus.UNDESIRED_STATE_OBSERVED,
        ActionExecutionReconciliationStatus.INCONCLUSIVE,
    ],
)
def test_terminal_views_are_never_stale_or_recoverable(
    execution_context,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: ActionExecutionReconciliationStatus,
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    with sessions() as session:
        reconciliation = session.scalar(
            select(ActionExecutionReconciliationRecord)
        )
        reconciliation.status = terminal_status
        reconciliation.completed_at = NOW
        session.commit()

    response = client.get(view_url(execution_id, attempt_id))

    assert response.status_code == 200
    assert response.json()["status"] == terminal_status.value
    assert response.json()["is_stale"] is False
    assert response.json()["recoverable"] is False
    assert response.json()["recovery_block_reason"] == (
        "reconciliation_not_running"
    )


def test_missing_resources_and_ownership_return_structured_errors(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    other_execution_id, other_attempt_id, _ = seed_recovery_context(
        sessions,
        invocation_started_at=OLD,
        execution_status=ActionExecutionStatus.OUTCOME_UNKNOWN,
        attempt_status=ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
        failure_cause=FailureCause.PROCESS_INTERRUPTED,
        outcome_certainty=OutcomeCertainty.UNKNOWN,
    )

    missing_execution = client.get(view_url(999999, attempt_id))
    missing_attempt = client.get(view_url(execution_id, 999999))
    mismatch = client.get(view_url(execution_id, other_attempt_id))

    assert other_execution_id != execution_id
    assert missing_execution.status_code == 404
    assert missing_execution.json()["detail"]["code"] == (
        "action_execution_not_found"
    )
    assert missing_attempt.status_code == 404
    assert missing_attempt.json()["detail"]["code"] == (
        "execution_attempt_not_found"
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "execution_attempt_mismatch"


def test_missing_reconciliation_is_404_and_is_not_created(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    response = client.get(view_url(execution_id, attempt_id))
    with sessions() as session:
        reconciliations = list(
            session.scalars(select(ActionExecutionReconciliationRecord))
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == (
        "action_execution_reconciliation_not_found"
    )
    assert reconciliations == []


def test_repeated_get_has_no_tools_events_or_persistence_side_effects(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    set_reconciliation_started_at(sessions, NOW - timedelta(seconds=300))
    monkeypatch.setattr(
        ActionExecutionReconciliationService,
        "_utc_now",
        staticmethod(lambda: NOW),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("read-only view cannot resolve or execute tools")

    app.dependency_overrides[get_controlled_tools] = forbidden
    monkeypatch.setattr(MonitoringTools, "get_application_health", forbidden)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", forbidden)
    before = persisted_snapshot(sessions, execution_id)

    first = client.get(view_url(execution_id, attempt_id))
    second = client.get(view_url(execution_id, attempt_id))
    after = persisted_snapshot(sessions, execution_id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["is_stale"] is True
    assert first.json()["recoverable"] is True
    assert first.json()["recovery_block_reason"] is None
    assert after == before


@pytest.mark.parametrize(
    ('blocker', 'reason'),
    [
        ('invocation', 'invocation_not_started'),
        ('execution', 'execution_not_outcome_unknown'),
        ('attempt', 'attempt_not_outcome_unknown'),
        ('certainty', 'outcome_certainty_not_unknown'),
    ],
)
def test_stale_view_reports_state_eligibility_blockers(
    execution_context,
    monkeypatch: pytest.MonkeyPatch,
    blocker: str,
    reason: str,
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        if blocker == 'invocation':
            attempt.invocation_started_at = None
        elif blocker == 'execution':
            execution.status = ActionExecutionStatus.FAILED
        elif blocker == 'attempt':
            attempt.status = ActionExecutionAttemptStatus.FAILED
        else:
            attempt.outcome_certainty = OutcomeCertainty.NOT_APPLIED
        session.commit()

    response = client.get(view_url(execution_id, attempt_id))

    assert response.status_code == 200
    assert response.json()['is_stale'] is True
    assert response.json()['recoverable'] is False
    assert response.json()['recovery_block_reason'] == reason


@pytest.mark.parametrize(
    ('blocker', 'reason'),
    [
        ('proposal', 'proposal_unavailable'),
        ('policy', 'policy_unavailable'),
        ('mismatch', 'policy_mismatch'),
    ],
)
def test_stale_view_reports_governance_blockers(
    execution_context,
    monkeypatch: pytest.MonkeyPatch,
    blocker: str,
    reason: str,
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    if blocker == 'proposal':
        monkeypatch.setattr(
            ActionExecutionReconciliationRepository,
            'get_proposal',
            lambda self, proposal_id: None,
        )
    else:
        with sessions() as session:
            execution = session.get(ActionExecutionRecord, execution_id)
            reconciliation = session.scalar(
                select(ActionExecutionReconciliationRecord)
            )
            if blocker == 'policy':
                execution.capability_name = 'unsupported_capability'
            else:
                reconciliation.observer = 'unexpected_observer'
            session.commit()

    response = client.get(view_url(execution_id, attempt_id))

    assert response.status_code == 200
    assert response.json()['is_stale'] is True
    assert response.json()['recoverable'] is False
    assert response.json()['recovery_block_reason'] == reason


def test_canonical_blocker_precedes_invocation_blocker(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, _, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    with sessions() as session:
        second = ActionExecutionAttemptRecord(
            execution_id=execution_id,
            attempt_number=2,
            status=ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            claimed_at=NOW,
            invocation_started_at=None,
            completed_at=NOW,
            error={'code': 'legacy_test'},
            failure_cause=FailureCause.LEGACY_UNCLASSIFIED,
            outcome_certainty=OutcomeCertainty.UNKNOWN,
        )
        session.add(second)
        session.flush()
        reconciliation = session.scalar(
            select(ActionExecutionReconciliationRecord)
        )
        reconciliation.attempt_id = second.id
        session.commit()
        second_id = second.id

    response = client.get(view_url(execution_id, second_id))

    assert response.status_code == 200
    assert response.json()['is_stale'] is True
    assert response.json()['recoverable'] is False
    assert response.json()['recovery_block_reason'] == 'attempt_not_canonical'
