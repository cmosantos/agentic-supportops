from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
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
from domain.ai import InvestigationEventType
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
)
from repositories.investigation_repository import InvestigationRepository
from tools.actions import ActionTools
from tools.monitoring import MonitoringTools

from tests.test_action_execution import (
    approved_execution,
    execute_url,
    execution_context,
)


def reconciliation_url(execution_id: int, attempt_id: int) -> str:
    return f"/action-executions/{execution_id}/attempts/{attempt_id}/reconcile"


def create_unknown_execution(
    client: TestClient,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
    *,
    desired_state_reached: bool,
) -> tuple[int, int, int]:
    investigation_id, proposal_id = approved_execution(client)

    def lose_acknowledgement(self, target, service_name):
        if desired_state_reached:
            self._repository.restart_application(target)
        raise RuntimeError("simulated acknowledgement loss")

    monkeypatch.setattr(
        ActionTools, "restart_simulated_service", lose_acknowledgement
    )
    response = client.post(execute_url(investigation_id, proposal_id))
    assert response.status_code == 200
    assert response.json()["status"] == "outcome_unknown"
    with sessions() as session:
        execution = session.scalar(select(ActionExecutionRecord))
        attempt = session.scalar(select(ActionExecutionAttemptRecord))
        return execution.id, attempt.id, investigation_id


def persisted_state(sessions, execution_id: int):
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
                            InvestigationEventType.RECONCILIATION_REQUESTED,
                            InvestigationEventType.RECONCILIATION_STARTED,
                            InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED,
                            InvestigationEventType.RECONCILIATION_UNDESIRED_STATE_OBSERVED,
                            InvestigationEventType.RECONCILIATION_INCONCLUSIVE,
                        )
                    )
                )
                .order_by(InvestigationEventRecord.sequence)
            )
        )
        return execution, attempts, reconciliations, events


def test_desired_state_completes_execution_but_preserves_unknown_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=True
    )

    response = client.post(reconciliation_url(execution_id, attempt_id))
    execution, attempts, reconciliations, events = persisted_state(
        sessions, execution_id
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "desired_state_observed"
    assert response.json()["observer"] == "get_application_health"
    assert response.json()["expected_outcome"] == {"state": "healthy"}
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert execution.completion_basis == ActionExecutionCompletionBasis.RECONCILIATION
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN
    assert len(reconciliations) == 1
    assert [event.event_type for event in events] == [
        InvestigationEventType.RECONCILIATION_REQUESTED,
        InvestigationEventType.RECONCILIATION_STARTED,
        InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED,
    ]


def test_undesired_state_preserves_unknown_execution_and_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    response = client.post(reconciliation_url(execution_id, attempt_id))
    execution, attempts, _, events = persisted_state(sessions, execution_id)

    assert response.status_code == 200
    assert response.json()["status"] == "undesired_state_observed"
    assert response.json()["observed_outcome"] == {"state": "degraded"}
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completion_basis is None
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert events[-1].event_type == (
        InvestigationEventType.RECONCILIATION_UNDESIRED_STATE_OBSERVED
    )


def test_observer_failure_is_inconclusive_and_safe(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    def observer_failure(self, application_id):
        raise RuntimeError("sensitive observer detail")

    monkeypatch.setattr(MonitoringTools, "get_application_health", observer_failure)
    response = client.post(reconciliation_url(execution_id, attempt_id))
    execution, attempts, _, events = persisted_state(sessions, execution_id)

    assert response.status_code == 200
    assert response.json()["status"] == "inconclusive"
    assert response.json()["error"] == {
        "code": "observer_failure",
        "message": "Unable to collect reliable reconciliation evidence",
    }
    assert "sensitive" not in response.text
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert events[-1].event_type == InvestigationEventType.RECONCILIATION_INCONCLUSIVE


def test_duplicate_reconciliation_observes_once_and_creates_no_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    calls = 0
    original = MonitoringTools.get_application_health

    def counted(self, application_id):
        nonlocal calls
        calls += 1
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", counted)
    url = reconciliation_url(execution_id, attempt_id)
    first = client.post(url)
    second = client.post(url)
    _, attempts, reconciliations, events = persisted_state(sessions, execution_id)

    assert first.json() == second.json()
    assert calls == 1
    assert len(attempts) == 1
    assert len(reconciliations) == 1
    assert len(events) == 3


def test_concurrent_reconciliation_has_one_observation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    calls = 0
    original = MonitoringTools.get_application_health

    def counted(self, application_id):
        nonlocal calls
        calls += 1
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", counted)
    url = reconciliation_url(execution_id, attempt_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post(url), range(2)))
    _, attempts, reconciliations, events = persisted_state(sessions, execution_id)

    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert calls == 1
    assert len(attempts) == 1
    assert len(reconciliations) == 1
    assert len(events) == 3


def test_terminal_event_failure_rolls_back_execution_completion(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=True
    )
    original = InvestigationRepository.record_event

    def fail_terminal(self, investigation_id, runtime, event_type, sequence, **fields):
        if event_type == InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED:
            raise RuntimeError("simulated reconciliation event failure")
        return original(
            self, investigation_id, runtime, event_type, sequence, **fields
        )

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_terminal)
    with pytest.raises(RuntimeError, match="reconciliation event failure"):
        client.post(reconciliation_url(execution_id, attempt_id))
    execution, attempts, reconciliations, events = persisted_state(
        sessions, execution_id
    )

    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completion_basis is None
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert reconciliations[0].status.value == "running"
    assert [event.event_type for event in events] == [
        InvestigationEventType.RECONCILIATION_REQUESTED,
        InvestigationEventType.RECONCILIATION_STARTED,
    ]


def test_request_body_cannot_control_reconciliation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    response = client.post(
        reconciliation_url(execution_id, attempt_id),
        json={
            "observer": "restart_simulated_service",
            "target": "OTHER",
            "expected_state": "failed",
            "arguments": {"command": "arbitrary"},
        },
    )

    assert response.status_code == 200
    assert response.json()["observer"] == "get_application_health"
    assert response.json()["expected_outcome"] == {"state": "healthy"}


def test_missing_and_ineligible_contexts_return_structured_errors(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )

    missing_execution = client.post(reconciliation_url(999999, attempt_id))
    missing_attempt = client.post(reconciliation_url(execution_id, 999999))
    with sessions() as session:
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        attempt.invocation_started_at = None
        session.commit()
    not_started = client.post(reconciliation_url(execution_id, attempt_id))

    assert missing_execution.status_code == 404
    assert missing_execution.json()["detail"]["code"] == "action_execution_not_found"
    assert missing_attempt.status_code == 404
    assert missing_attempt.json()["detail"]["code"] == "execution_attempt_not_found"
    assert not_started.status_code == 409
    assert not_started.json()["detail"]["code"] == (
        "execution_attempt_invocation_not_started"
    )


def test_policy_denial_does_not_create_reconciliation(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    execution_id, attempt_id, _ = create_unknown_execution(
        client, sessions, monkeypatch, desired_state_reached=False
    )
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        execution.capability_name = "unsupported_capability"
        session.commit()

    response = client.post(reconciliation_url(execution_id, attempt_id))
    _, attempts, reconciliations, events = persisted_state(sessions, execution_id)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "reconciliation_policy_denied"
    assert len(attempts) == 1
    assert reconciliations == []
    assert events == []
