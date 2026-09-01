from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from api.dependencies import get_controlled_tools, get_db_session
from db.base import Base
from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionRecord,
    InvestigationEventRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionCompletionBasis,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.ai import InvestigationEventType
from domain.investigation import ToolResult
from main import app
from repositories.action_execution_attempt_repository import (
    ActionExecutionAttemptRepository,
    InvalidActionExecutionAttemptTransitionError,
)
from repositories.action_execution_repository import ActionExecutionRepository
from repositories.investigation_repository import InvestigationRepository
from simulation.repository import SimulationRepository
from simulation.seed import seed_catalog
from services.execution_policy import ExecutionPolicy, ExecutionPolicyDeniedError
from services.tool_registry import InvestigationToolRegistry
from tools.actions import ActionTools

from tests.test_action_proposals import (
    proposal_payload,
    proposal_url,
    run_actionable_investigation,
)


def executable_payload(evidence_id: int, target: str = "SUPPORT-API") -> dict:
    payload = proposal_payload(evidence_id, "restart_simulated_service")
    payload["target"] = target
    payload["parameters"] = {"service_name": "SupportApi"}
    return payload


def create_proposal(client: TestClient, *, target: str = "SUPPORT-API") -> tuple[int, dict]:
    investigation = run_actionable_investigation(client)
    investigation_id = investigation["investigation"]["id"]
    response = client.post(
        proposal_url(investigation_id),
        json=executable_payload(investigation["evidence"][0]["id"], target),
    )
    assert response.status_code == 201, response.text
    return investigation_id, response.json()


def create_action_proposal(
    client: TestClient, action_type: str, target: str
) -> tuple[int, dict]:
    investigation = run_actionable_investigation(client)
    investigation_id = investigation["investigation"]["id"]
    payload = proposal_payload(investigation["evidence"][0]["id"], action_type)
    payload["target"] = target
    response = client.post(proposal_url(investigation_id), json=payload)
    assert response.status_code == 201, response.text
    return investigation_id, response.json()


def execute_url(investigation_id: int, proposal_id: int) -> str:
    return f"{proposal_url(investigation_id)}/{proposal_id}/execute"


@pytest.fixture
def execution_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> Generator[
    tuple[TestClient, sessionmaker, SimulationRepository], None, None
]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'execution-attempt.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with sessions() as session:
        seed_catalog(session)
    monkeypatch.setattr("main.initialize_database", lambda: None)

    def override_session() -> Generator[Session, None, None]:
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_session] = override_session
    controlled_repository = SimulationRepository()
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(
        controlled_repository
    )
    with TestClient(app) as client:
        yield client, sessions, controlled_repository
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def approved_execution(client: TestClient, *, target: str = "SUPPORT-API") -> tuple[int, int]:
    investigation_id, proposal = create_proposal(client, target=target)
    response = client.post(
        f"{proposal_url(investigation_id)}/{proposal['id']}/approve"
    )
    assert response.status_code == 200, response.text
    return investigation_id, proposal["id"]


def persisted_execution_state(sessions: sessionmaker) -> tuple:
    with sessions() as session:
        execution = session.scalar(select(ActionExecutionRecord))
        attempts = list(session.scalars(select(ActionExecutionAttemptRecord)))
        events = list(
            session.scalars(
                select(InvestigationEventRecord).where(
                    InvestigationEventRecord.event_type.in_(
                        (
                            InvestigationEventType.EXECUTION_REQUESTED,
                            InvestigationEventType.EXECUTION_STARTED,
                            InvestigationEventType.EXECUTION_COMPLETED,
                            InvestigationEventType.EXECUTION_FAILED,
                            InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN,
                        )
                    )
                ).order_by(InvestigationEventRecord.sequence)
            )
        )
        return execution, attempts, events


def test_pending_and_rejected_proposals_cannot_execute(seeded_client: TestClient) -> None:
    investigation_id, pending = create_proposal(seeded_client)
    pending_response = seeded_client.post(execute_url(investigation_id, pending["id"]))
    seeded_client.post(
        f"{proposal_url(investigation_id)}/{pending['id']}/reject",
        json={"reason": "Operator rejected the action."},
    )
    rejected_response = seeded_client.post(execute_url(investigation_id, pending["id"]))

    assert pending_response.status_code == 409
    assert rejected_response.status_code == 409
    assert pending_response.json()["detail"]["code"] == "proposal_not_approved"


@pytest.mark.parametrize(
    ("action_type", "target"),
    [
        ("unlock_simulated_user", "USR-ALICE"),
        ("reset_simulated_application_state", "SUPPORT-API"),
    ],
)
def test_new_mutations_require_approval(
    seeded_client: TestClient, action_type: str, target: str
) -> None:
    investigation_id, proposal = create_action_proposal(
        seeded_client, action_type, target
    )

    response = seeded_client.post(execute_url(investigation_id, proposal["id"]))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "proposal_not_approved"


@pytest.mark.parametrize(
    ("action_type", "target", "method_name", "result_key", "expected_value"),
    [
        (
            "unlock_simulated_user",
            "USR-ALICE",
            "unlock_simulated_user",
            "current_locked",
            False,
        ),
        (
            "reset_simulated_application_state",
            "SUPPORT-API",
            "reset_simulated_application_state",
            "current_state",
            "healthy",
        ),
    ],
)
def test_approved_new_mutations_use_persisted_target_and_execute_once(
    seeded_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action_type: str,
    target: str,
    method_name: str,
    result_key: str,
    expected_value,
) -> None:
    investigation_id, proposal = create_action_proposal(
        seeded_client, action_type, target
    )
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    calls = []
    original = getattr(ActionTools, method_name)

    def counted(self, target):
        calls.append(target)
        return original(self, target)

    monkeypatch.setattr(ActionTools, method_name, counted)
    url = execute_url(investigation_id, proposal["id"])
    first = seeded_client.post(
        url,
        json={"capability": "run_arbitrary_command", "target": "OTHER"},
    )
    duplicate = seeded_client.post(url)

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["capability_name"] == action_type
    assert first.json()["result"]["data"]["target"] == target
    assert first.json()["result"]["data"][result_key] == expected_value
    assert duplicate.json() == first.json()
    assert calls == [target]


def test_approved_proposal_executes_persisted_action_once_and_is_audited(
    seeded_client: TestClient, monkeypatch
) -> None:
    investigation_id, proposal = create_proposal(seeded_client)
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    calls = 0
    original = ActionTools.restart_simulated_service

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        return original(self, target, service_name)

    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)
    url = execute_url(investigation_id, proposal["id"])
    first = seeded_client.post(
        url,
        json={"capability": "run_arbitrary_command", "target": "OTHER"},
    )
    second = seeded_client.post(url)
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"
    assert first.json()["capability_name"] == "restart_simulated_service"
    assert first.json()["result"]["data"] == {
        "target": "SUPPORT-API",
        "service_name": "SupportApi",
        "previous_state": "degraded",
        "current_state": "healthy",
        "restarted": True,
    }
    assert second.json() == first.json()
    assert calls == 1
    assert [item["event_type"] for item in events[-3:]] == [
        "execution_requested",
        "execution_started",
        "execution_completed",
    ]
    assert all(item["metadata"]["proposal_id"] == proposal["id"] for item in events[-3:])


def test_success_persists_first_attempt_and_acknowledged_completion(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0
    original = ActionTools.restart_simulated_service

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        with sessions() as session:
            durable_attempt = session.scalar(select(ActionExecutionAttemptRecord))
            assert durable_attempt.status == ActionExecutionAttemptStatus.RUNNING
            assert durable_attempt.invocation_started_at is not None
            original_started_at = durable_attempt.invocation_started_at
            with pytest.raises(InvalidActionExecutionAttemptTransitionError):
                ActionExecutionAttemptRepository(session).mark_invocation_started(
                    durable_attempt, original_started_at + timedelta(seconds=1)
                )
            session.rollback()
        with sessions() as session:
            durable_attempt = session.scalar(select(ActionExecutionAttemptRecord))
            assert durable_attempt.invocation_started_at == original_started_at
        return original(self, target, service_name)

    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)
    response = client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert response.status_code == 200, response.text
    assert calls == 1
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert execution.completion_basis == ActionExecutionCompletionBasis.ACKNOWLEDGED_RESULT
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.execution_id == execution.id
    assert attempt.attempt_number == 1
    assert attempt.status == ActionExecutionAttemptStatus.COMPLETED
    assert attempt.invocation_started_at is not None
    assert attempt.outcome_certainty == OutcomeCertainty.APPLIED_ACKNOWLEDGED
    assert attempt.result == execution.result == response.json()["result"]
    assert [event.event_type for event in events] == [
        InvestigationEventType.EXECUTION_REQUESTED,
        InvestigationEventType.EXECUTION_STARTED,
        InvestigationEventType.EXECUTION_COMPLETED,
    ]


def test_known_pre_mutation_failure_persists_not_applied_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client, target="UNKNOWN-APP")
    calls = 0
    original = ActionTools.restart_simulated_service

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        return original(self, target, service_name)

    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)
    url = execute_url(investigation_id, proposal_id)
    response = client.post(url)
    duplicate = client.post(url)
    execution, attempts, events = persisted_execution_state(sessions)

    assert response.status_code == 200, response.text
    assert duplicate.json() == response.json()
    assert calls == 1
    assert response.json()["error"]["code"] == "application_not_found"
    assert execution.status == ActionExecutionStatus.FAILED
    assert execution.completion_basis is None
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.FAILED
    assert attempts[0].invocation_started_at is not None
    assert attempts[0].failure_cause == FailureCause.TOOL_REJECTED
    assert attempts[0].outcome_certainty == OutcomeCertainty.NOT_APPLIED
    assert attempts[0].error == execution.error == response.json()["error"]
    assert events[-1].event_type == InvestigationEventType.EXECUTION_FAILED


def test_duplicate_execution_returns_canonical_record_without_second_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0
    original = ActionTools.restart_simulated_service

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        return original(self, target, service_name)

    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)
    url = execute_url(investigation_id, proposal_id)
    first = client.post(url)
    second = client.post(url)
    execution, attempts, events = persisted_execution_state(sessions)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["id"] == execution.id
    assert calls == 1
    assert [attempt.attempt_number for attempt in attempts] == [1]
    assert len(events) == 3


def test_unexpected_capability_exception_becomes_outcome_unknown(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0

    def fail_after_unknown_progress(self, target, service_name):
        nonlocal calls
        calls += 1
        raise RuntimeError("simulated ambiguous capability exception")

    monkeypatch.setattr(
        ActionTools, "restart_simulated_service", fail_after_unknown_progress
    )
    url = execute_url(investigation_id, proposal_id)
    response = client.post(url)
    duplicate = client.post(url)
    execution, attempts, events = persisted_execution_state(sessions)

    assert response.status_code == 200
    assert duplicate.json() == response.json()
    assert response.json()["status"] == "outcome_unknown"
    assert response.json()["error"]["code"] == "capability_outcome_unknown"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completed_at is None
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].invocation_started_at is not None
    assert attempts[0].failure_cause == FailureCause.TOOL_EXCEPTION
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN
    assert (
        events[-1].event_type
        == InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN
    )
    assert events[-1].event_metadata["attempt_id"] == attempts[0].id
    assert events[-1].event_metadata["failure_cause"] == "tool_exception"


def test_side_effect_then_exception_preserves_mutation_as_outcome_unknown(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, simulation = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0

    def mutate_then_fail(self, target, service_name):
        nonlocal calls
        calls += 1
        self._repository.restart_application(target)
        raise RuntimeError("acknowledgement lost after mutation")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", mutate_then_fail)
    url = execute_url(investigation_id, proposal_id)
    first = client.post(url)
    second = client.post(url)
    execution, attempts, events = persisted_execution_state(sessions)

    assert simulation.get_application_for_action("SUPPORT-API").status == "healthy"
    assert first.json() == second.json()
    assert first.json()["status"] == "outcome_unknown"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].failure_cause == FailureCause.TOOL_EXCEPTION
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN
    assert events[-1].event_type == (
        InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN
    )


def test_malformed_result_becomes_outcome_unknown_without_retry(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0

    def malformed(self, target, service_name):
        nonlocal calls
        calls += 1
        return ToolResult(
            tool="restart_simulated_service",
            resource=target,
            success=True,
        )

    monkeypatch.setattr(ActionTools, "restart_simulated_service", malformed)
    url = execute_url(investigation_id, proposal_id)
    first = client.post(url)
    second = client.post(url)
    execution, attempts, _ = persisted_execution_state(sessions)

    assert first.json() == second.json()
    assert first.json()["status"] == "outcome_unknown"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert attempts[0].failure_cause == FailureCause.RESULT_INVALID
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN


def test_timeout_after_invocation_becomes_outcome_unknown(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)

    def timeout(self, target, service_name):
        raise TimeoutError("simulated synchronous capability timeout")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", timeout)
    response = client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, _ = persisted_execution_state(sessions)

    assert response.json()["status"] == "outcome_unknown"
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert attempts[0].failure_cause == FailureCause.TIMEOUT
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN


def test_concurrent_execution_claim_invokes_capability_once(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    claim_barrier = Barrier(2)
    calls = 0
    calls_lock = Lock()
    original_start = ActionExecutionRepository.start
    original_action = ActionTools.restart_simulated_service

    def synchronized_start(self, proposal, runtime):
        claim_barrier.wait(timeout=5)
        return original_start(self, proposal, runtime)

    def counted(self, target, service_name):
        nonlocal calls
        with calls_lock:
            calls += 1
        return original_action(self, target, service_name)

    monkeypatch.setattr(ActionExecutionRepository, "start", synchronized_start)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)
    url = execute_url(investigation_id, proposal_id)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: client.post(url), range(2)))
    execution, attempts, events = persisted_execution_state(sessions)

    assert all(response.status_code == 200 for response in responses)
    assert {response.json()["id"] for response in responses} == {execution.id}
    assert {response.json()["status"] for response in responses} <= {
        "running",
        "completed",
    }
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert calls == 1
    assert len(attempts) == 1
    assert attempts[0].attempt_number == 1
    assert len(events) == 3


def test_initial_transaction_failure_rolls_back_before_capability(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0
    original_event = InvestigationRepository.record_event

    def fail_started_event(self, investigation_id, runtime, event_type, sequence, **fields):
        if event_type == InvestigationEventType.EXECUTION_STARTED:
            raise RuntimeError("simulated start event persistence failure")
        return original_event(
            self, investigation_id, runtime, event_type, sequence, **fields
        )

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_started_event)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)

    with pytest.raises(RuntimeError, match="start event persistence failure"):
        client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert calls == 0
    assert execution is None
    assert attempts == []
    assert events == []


def test_invocation_marker_persistence_failure_prevents_capability(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    commit_calls = 0
    capability_calls = 0
    original_commit = ActionExecutionRepository._commit

    def fail_marker_commit(self):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("simulated invocation marker persistence failure")
        return original_commit(self)

    def counted(*args, **kwargs):
        nonlocal capability_calls
        capability_calls += 1

    monkeypatch.setattr(ActionExecutionRepository, "_commit", fail_marker_commit)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)

    with pytest.raises(RuntimeError, match="invocation marker persistence failure"):
        client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert capability_calls == 0
    assert execution.status == ActionExecutionStatus.RUNNING
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.RUNNING
    assert attempts[0].invocation_started_at is None
    assert [event.event_type for event in events] == [
        InvestigationEventType.EXECUTION_REQUESTED,
        InvestigationEventType.EXECUTION_STARTED,
    ]


def test_terminal_failure_is_classified_unknown_in_separate_transaction(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    original_event = InvestigationRepository.record_event
    original_action = ActionTools.restart_simulated_service
    calls = 0

    def fail_completed_event(
        self, investigation_id, runtime, event_type, sequence, **fields
    ):
        if event_type == InvestigationEventType.EXECUTION_COMPLETED:
            raise RuntimeError("simulated terminal event persistence failure")
        return original_event(
            self, investigation_id, runtime, event_type, sequence, **fields
        )

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        return original_action(self, target, service_name)

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_completed_event)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)

    response = client.post(execute_url(investigation_id, proposal_id))
    duplicate = client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert response.status_code == duplicate.status_code == 200
    assert response.json() == duplicate.json()
    assert response.json()["status"] == "outcome_unknown"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completed_at is None
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempts[0].completed_at is not None
    assert attempts[0].failure_cause == FailureCause.TERMINAL_PERSISTENCE_FAILED
    assert attempts[0].outcome_certainty == OutcomeCertainty.UNKNOWN
    assert [event.event_type for event in events] == [
        InvestigationEventType.EXECUTION_REQUESTED,
        InvestigationEventType.EXECUTION_STARTED,
        InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN,
    ]


def test_terminal_and_fallback_persistence_failure_leaves_durable_running(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    original_event = InvestigationRepository.record_event
    original_action = ActionTools.restart_simulated_service
    calls = 0

    def fail_terminal_events(
        self, investigation_id, runtime, event_type, sequence, **fields
    ):
        if event_type in {
            InvestigationEventType.EXECUTION_COMPLETED,
            InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN,
        }:
            raise RuntimeError("simulated terminal persistence unavailable")
        return original_event(
            self, investigation_id, runtime, event_type, sequence, **fields
        )

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
        return original_action(self, target, service_name)

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_terminal_events)
    monkeypatch.setattr(ActionTools, "restart_simulated_service", counted)

    with pytest.raises(RuntimeError, match="terminal persistence unavailable"):
        client.post(execute_url(investigation_id, proposal_id))
    duplicate = client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert duplicate.json()["status"] == "running"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.RUNNING
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.RUNNING
    assert attempts[0].invocation_started_at is not None
    assert attempts[0].failure_cause is None
    assert [event.event_type for event in events] == [
        InvestigationEventType.EXECUTION_REQUESTED,
        InvestigationEventType.EXECUTION_STARTED,
    ]


def test_execution_policy_blocks_unknown_capability() -> None:
    with pytest.raises(ExecutionPolicyDeniedError):
        ExecutionPolicy().authorize("run_arbitrary_command")


def test_capability_failure_is_persisted_without_changing_approval(
    seeded_client: TestClient,
) -> None:
    investigation_id, proposal = create_proposal(seeded_client, target="UNKNOWN-APP")
    approved = seeded_client.post(
        f"{proposal_url(investigation_id)}/{proposal['id']}/approve"
    ).json()

    response = seeded_client.post(execute_url(investigation_id, proposal["id"]))
    proposals = seeded_client.get(proposal_url(investigation_id)).json()
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == {
        "code": "application_not_found",
        "message": "Application not found",
    }
    assert approved["approval_status"] == "approved"
    assert proposals[0]["approval_status"] == "approved"
    assert events[-1]["event_type"] == "execution_failed"


def test_execution_record_survives_session_reload_and_database_rejects_duplicates(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'execution.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        # The database invariant is independent of service-level existence checks.
        first = ActionExecutionRecord(
            proposal_id=1,
            incident_id=1,
            capability_name="restart_simulated_service",
            status=ActionExecutionStatus.RUNNING,
            started_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
        )
        session.add(first)
        try:
            session.commit()
        except IntegrityError:
            # Foreign keys may be enabled by a future SQLite configuration.
            session.rollback()
            return
    with sessions() as reloaded:
        stored = reloaded.scalar(select(ActionExecutionRecord))
        assert stored is not None
        reloaded.add(
            ActionExecutionRecord(
                proposal_id=1,
                incident_id=1,
                capability_name="restart_simulated_service",
                status=ActionExecutionStatus.RUNNING,
                started_at=datetime(2026, 8, 29, 12, 0, 1, tzinfo=timezone.utc),
            )
        )
        try:
            reloaded.commit()
            raise AssertionError("duplicate proposal execution was accepted")
        except IntegrityError:
            reloaded.rollback()
    engine.dispose()


def test_execution_capability_is_registered_but_not_advertised_to_investigators() -> None:
    registry = InvestigationToolRegistry()
    mutable_capabilities = {
        "restart_simulated_service",
        "unlock_simulated_user",
        "reset_simulated_application_state",
    }
    assert mutable_capabilities.isdisjoint(registry.names)
    assert mutable_capabilities.isdisjoint(
        {item["name"] for item in registry.openai_tools}
    )
    _, blocked = registry.dispatch(
        "restart_simulated_service",
        '{"target":"SUPPORT-API","service_name":"SupportApi"}',
    )
    assert blocked.success is False
    assert blocked.error.code == "unknown_tool"
    result = registry.execute(
        "restart_simulated_service",
        {"target": "SUPPORT-API", "service_name": "SupportApi"},
    )
    assert result.success is True


def test_unlock_mutates_only_the_local_simulated_user_state() -> None:
    simulation = SimulationRepository(locked_users={"USR-ALICE"})
    registry = InvestigationToolRegistry(simulation)

    before = registry.execute("get_account_status", {"user_id": "USR-ALICE"})
    result = registry.execute("unlock_simulated_user", {"target": "USR-ALICE"})
    after = registry.execute("get_account_status", {"user_id": "USR-ALICE"})

    assert before.data["locked"] is True
    assert result.success is True
    assert after.data["locked"] is False
