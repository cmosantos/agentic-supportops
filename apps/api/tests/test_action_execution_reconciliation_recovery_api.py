from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Event, Lock

import pytest
from sqlalchemy import select

from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionReconciliationRecord,
    ActionExecutionRecord,
    InvestigationEventRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionCompletionBasis,
    ActionExecutionStatus,
    OutcomeCertainty,
)
from domain.action_execution_reconciliation import (
    ActionExecutionReconciliationStatus,
)
from domain.ai import InvestigationEventType
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
)
from repositories.investigation_repository import InvestigationRepository
from services.verification_policy import VerificationPolicy
from tools.actions import ActionTools
from tools.monitoring import MonitoringTools

from tests.test_action_execution import execution_context
from tests.test_action_execution_reconciliation_api import (
    create_unknown_execution,
)


def recovery_url(execution_id: int, attempt_id: int) -> str:
    return (
        f"/action-executions/{execution_id}/attempts/{attempt_id}"
        "/reconciliation/recover"
    )


def make_running_reconciliation(
    client,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
    *,
    desired_state_reached: bool,
    stale: bool = True,
) -> tuple[int, int, int]:
    execution_id, attempt_id, investigation_id = create_unknown_execution(
        client,
        sessions,
        monkeypatch,
        desired_state_reached=desired_state_reached,
    )
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        repository = ActionExecutionReconciliationRepository(session)
        proposal = repository.get_proposal(execution.proposal_id)
        strategy = VerificationPolicy().strategy_for(execution.capability_name)
        reconciliation, created = repository.start(
            execution,
            attempt,
            proposal,
            repository.runtime_for(proposal),
            strategy.observer,
            {"state": strategy.expected_state},
        )
        assert created is True
        if stale:
            reconciliation.started_at = datetime.now(timezone.utc) - timedelta(
                seconds=360
            )
            session.commit()
    return execution_id, attempt_id, investigation_id


def recovery_state(sessions, execution_id: int):
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
        events = list(
            session.scalars(
                select(InvestigationEventRecord)
                .where(
                    InvestigationEventRecord.event_type.in_(
                        (
                            InvestigationEventType.RECONCILIATION_RECOVERY_REQUESTED,
                            InvestigationEventType.RECONCILIATION_RECOVERY_STARTED,
                            InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED,
                            InvestigationEventType.RECONCILIATION_UNDESIRED_STATE_OBSERVED,
                            InvestigationEventType.RECONCILIATION_INCONCLUSIVE,
                            InvestigationEventType.EXECUTION_COMPLETED,
                        )
                    )
                )
                .order_by(InvestigationEventRecord.sequence)
            )
        )
        return execution, attempts, reconciliations, events


def test_stale_recovery_observes_desired_state_on_same_reconciliation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    with sessions() as session:
        canonical_id = session.scalar(
            select(ActionExecutionReconciliationRecord.id)
        )

    def forbidden_action(*args, **kwargs):
        raise AssertionError("original action must never be reinvoked")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", forbidden_action)
    response = client.post(recovery_url(execution_id, attempt_id))
    execution, attempts, reconciliations, events = recovery_state(
        sessions, execution_id
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == canonical_id
    assert response.json()["status"] == "desired_state_observed"
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert execution.completion_basis == ActionExecutionCompletionBasis.RECONCILIATION
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN
    assert len(reconciliations) == 1
    assert [event.event_type for event in events] == [
        InvestigationEventType.RECONCILIATION_RECOVERY_REQUESTED,
        InvestigationEventType.RECONCILIATION_RECOVERY_STARTED,
        InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED,
        InvestigationEventType.EXECUTION_COMPLETED,
    ]


def test_stale_recovery_observes_undesired_state_without_inference(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    response = client.post(recovery_url(execution_id, attempt_id))
    execution, attempts, reconciliations, _ = recovery_state(sessions, execution_id)

    assert response.status_code == 200
    assert response.json()["status"] == "undesired_state_observed"
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completion_basis is None
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert reconciliations[0].status == (
        ActionExecutionReconciliationStatus.UNDESIRED_STATE_OBSERVED
    )


def test_stale_recovery_observer_failure_is_inconclusive(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    def fail_observer(*args, **kwargs):
        raise RuntimeError("sensitive observer failure")

    monkeypatch.setattr(MonitoringTools, "get_application_health", fail_observer)
    response = client.post(recovery_url(execution_id, attempt_id))
    execution, attempts, reconciliations, _ = recovery_state(sessions, execution_id)

    assert response.status_code == 200
    assert response.json()["status"] == "inconclusive"
    assert "sensitive" not in response.text
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert reconciliations[0].status == (
        ActionExecutionReconciliationStatus.INCONCLUSIVE
    )


def test_recent_running_reconciliation_is_rejected_without_observation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client,
        sessions,
        monkeypatch,
        desired_state_reached=False,
        stale=False,
    )
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("recent reconciliation must not be observed")

    monkeypatch.setattr(MonitoringTools, "get_application_health", counted)
    response = client.post(recovery_url(execution_id, attempt_id))
    _, _, reconciliations, events = recovery_state(sessions, execution_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "execution_reconciliation_not_stale"
    )
    assert calls == 0
    assert reconciliations[0].status == ActionExecutionReconciliationStatus.RUNNING
    assert events == []


@pytest.mark.parametrize(
    ("desired_state_reached", "observer_fails", "terminal_status"),
    [
        (True, False, "desired_state_observed"),
        (False, False, "undesired_state_observed"),
        (False, True, "inconclusive"),
    ],
)
def test_terminal_recovery_returns_canonical_without_new_observation_or_events(
    execution_context,
    monkeypatch: pytest.MonkeyPatch,
    desired_state_reached: bool,
    observer_fails: bool,
    terminal_status: str,
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client,
        sessions,
        monkeypatch,
        desired_state_reached=desired_state_reached,
    )
    original = MonitoringTools.get_application_health
    calls = 0

    def observed(self, application_id):
        nonlocal calls
        calls += 1
        if observer_fails:
            raise RuntimeError("observer unavailable")
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", observed)
    url = recovery_url(execution_id, attempt_id)
    first = client.post(url)
    _, attempts_before, reconciliations_before, events_before = recovery_state(
        sessions, execution_id
    )
    second = client.post(url)
    _, attempts_after, reconciliations_after, events_after = recovery_state(
        sessions, execution_id
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == terminal_status
    assert calls == 1
    assert len(attempts_before) == len(attempts_after) == 1
    assert len(reconciliations_before) == len(reconciliations_after) == 1
    assert [event.id for event in events_before] == [
        event.id for event in events_after
    ]


def test_concurrent_stale_recoveries_claim_one_read_only_observation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    entered = Event()
    release = Event()
    lock = Lock()
    calls = 0
    original = MonitoringTools.get_application_health

    def paused(self, application_id):
        nonlocal calls
        with lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", paused)
    url = recovery_url(execution_id, attempt_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(client.post, url)
        assert entered.wait(timeout=5)
        second = executor.submit(client.post, url)
        second_response = second.result(timeout=5)
        release.set()
        first_response = first.result(timeout=5)
    execution, attempts, reconciliations, _ = recovery_state(sessions, execution_id)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["detail"]["code"] == (
        "execution_reconciliation_not_stale"
    )
    assert calls == 1
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert len(attempts) == 1
    assert len(reconciliations) == 1


def test_terminal_persistence_failure_can_be_recovered_after_new_stale_window(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = make_running_reconciliation(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    original = InvestigationRepository.record_event
    fail_once = True

    def fail_first_terminal(
        self, investigation_id, runtime, event_type, sequence, **fields
    ):
        nonlocal fail_once
        if (
            fail_once
            and event_type
            == InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED
        ):
            fail_once = False
            raise RuntimeError("simulated terminal persistence failure")
        return original(
            self, investigation_id, runtime, event_type, sequence, **fields
        )

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_first_terminal)
    with pytest.raises(RuntimeError, match="terminal persistence failure"):
        client.post(recovery_url(execution_id, attempt_id))
    execution, attempts, reconciliations, events = recovery_state(
        sessions, execution_id
    )

    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completion_basis is None
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert reconciliations[0].status == ActionExecutionReconciliationStatus.RUNNING
    assert [event.event_type for event in events] == [
        InvestigationEventType.RECONCILIATION_RECOVERY_REQUESTED,
        InvestigationEventType.RECONCILIATION_RECOVERY_STARTED,
    ]

    with sessions() as session:
        reconciliation = session.get(
            ActionExecutionReconciliationRecord, reconciliations[0].id
        )
        reconciliation.started_at = datetime.now(timezone.utc) - timedelta(
            seconds=360
        )
        session.commit()
    recovered = client.post(recovery_url(execution_id, attempt_id))
    execution, attempts, reconciliations, _ = recovery_state(sessions, execution_id)

    assert recovered.status_code == 200
    assert recovered.json()["status"] == "desired_state_observed"
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert len(attempts) == 1
    assert len(reconciliations) == 1


def test_recovery_requires_existing_canonical_reconciliation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    response = client.post(recovery_url(execution_id, attempt_id))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == (
        "action_execution_reconciliation_not_found"
    )
