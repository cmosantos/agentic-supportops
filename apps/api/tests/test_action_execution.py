from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
from main import app
from repositories.action_execution_repository import ActionExecutionRepository
from repositories.investigation_repository import InvestigationRepository
from simulation.repository import SimulationRepository
from simulation.seed import seed_catalog
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


def execute_url(investigation_id: int, proposal_id: int) -> str:
    return f"{proposal_url(investigation_id)}/{proposal_id}/execute"


@pytest.fixture
def execution_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker], None, None]:
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
        yield client, sessions
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
    client, sessions = execution_context
    investigation_id, proposal_id = approved_execution(client)
    calls = 0
    original = ActionTools.restart_simulated_service

    def counted(self, target, service_name):
        nonlocal calls
        calls += 1
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
    assert attempt.invocation_started_at is None
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
    client, sessions = execution_context
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
    assert attempts[0].failure_cause == FailureCause.TOOL_REJECTED
    assert attempts[0].outcome_certainty == OutcomeCertainty.NOT_APPLIED
    assert attempts[0].error == execution.error == response.json()["error"]
    assert events[-1].event_type == InvestigationEventType.EXECUTION_FAILED


def test_duplicate_execution_returns_canonical_record_without_second_attempt(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = execution_context
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


def test_unexpected_capability_exception_preserves_failure_without_certainty(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = execution_context
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
    assert response.json()["status"] == "failed"
    assert response.json()["error"]["code"] == "capability_failure"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.FAILED
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.FAILED
    assert attempts[0].failure_cause == FailureCause.TOOL_EXCEPTION
    assert attempts[0].outcome_certainty is None
    assert events[-1].event_type == InvestigationEventType.EXECUTION_FAILED


def test_concurrent_execution_claim_invokes_capability_once(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = execution_context
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
    client, sessions = execution_context
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


def test_terminal_event_failure_rolls_back_attempt_and_aggregate_together(
    execution_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = execution_context
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

    with pytest.raises(RuntimeError, match="terminal event persistence failure"):
        client.post(execute_url(investigation_id, proposal_id))
    duplicate = client.post(execute_url(investigation_id, proposal_id))
    execution, attempts, events = persisted_execution_state(sessions)

    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "running"
    assert calls == 1
    assert execution.status == ActionExecutionStatus.RUNNING
    assert execution.completed_at is None
    assert len(attempts) == 1
    assert attempts[0].status == ActionExecutionAttemptStatus.RUNNING
    assert attempts[0].completed_at is None
    assert [event.event_type for event in events] == [
        InvestigationEventType.EXECUTION_REQUESTED,
        InvestigationEventType.EXECUTION_STARTED,
    ]


def test_execution_policy_blocks_other_approved_proposal(seeded_client: TestClient) -> None:
    investigation = run_actionable_investigation(seeded_client)
    investigation_id = investigation["investigation"]["id"]
    proposal = seeded_client.post(
        proposal_url(investigation_id),
        json=proposal_payload(investigation["evidence"][0]["id"]),
    ).json()
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")

    response = seeded_client.post(execute_url(investigation_id, proposal["id"]))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "execution_policy_denied"


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
    assert "restart_simulated_service" not in registry.names
    assert "restart_simulated_service" not in {
        item["name"] for item in registry.openai_tools
    }
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
