from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.dependencies import (
    get_agents_sdk_model,
    get_controlled_tools,
    get_db_session,
    get_responses_gateway,
)
from db.base import Base
from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionRecord,
    InvestigationEventRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.ai import InvestigationEventType
from integrations.responses_gateway import ResponsesGateway
from main import app
from services.tool_registry import InvestigationToolRegistry
from tools.actions import ActionTools
from tools.monitoring import MonitoringTools

from tests.test_action_execution_recovery import seed_recovery_context


STALE_AFTER_SECONDS = 300


@pytest.fixture
def recovery_api_context(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale-assessment-api.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("main.initialize_database", lambda: None)

    def override_session() -> Generator[Session, None, None]:
        with sessions() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry()
    with TestClient(app) as client:
        yield client, sessions
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def stale_time() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SECONDS + 60)


def assessment_url(execution_id: int, attempt_id: int) -> str:
    return (
        f"/action-executions/{execution_id}/attempts/{attempt_id}/stale-assessment"
    )


def seed_api_context(
    sessions,
    *,
    claimed_at: datetime | None = None,
    invocation_started_at: datetime | None = None,
    execution_status: ActionExecutionStatus = ActionExecutionStatus.RUNNING,
    attempt_status: ActionExecutionAttemptStatus = ActionExecutionAttemptStatus.RUNNING,
    failure_cause: FailureCause | None = None,
    outcome_certainty: OutcomeCertainty | None = None,
    result: dict | None = None,
) -> tuple[int, int, int]:
    return seed_recovery_context(
        sessions,
        claimed_at=claimed_at or stale_time(),
        invocation_started_at=invocation_started_at,
        execution_status=execution_status,
        attempt_status=attempt_status,
        failure_cause=failure_cause,
        outcome_certainty=outcome_certainty,
        result=result,
    )


def recovery_state(sessions, execution_id: int) -> tuple:
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempts = list(
            session.scalars(
                select(ActionExecutionAttemptRecord).where(
                    ActionExecutionAttemptRecord.execution_id == execution_id
                )
            )
        )
        events = list(
            session.scalars(
                select(InvestigationEventRecord).where(
                    InvestigationEventRecord.event_type
                    == InvestigationEventType.EXECUTION_ATTEMPT_INTERRUPTION_ASSESSED
                )
            )
        )
        return execution, attempts, events


@pytest.mark.parametrize(
    ("invocation_started_at", "execution_status", "attempt_status", "certainty"),
    [
        (
            None,
            "failed",
            "failed",
            "not_applied",
        ),
        (
            "stale",
            "outcome_unknown",
            "outcome_unknown",
            "unknown",
        ),
    ],
)
def test_stale_assessment_returns_canonical_pre_and_post_invocation_state(
    recovery_api_context,
    invocation_started_at,
    execution_status,
    attempt_status,
    certainty,
) -> None:
    client, sessions = recovery_api_context
    invocation = stale_time() if invocation_started_at else None
    execution_id, attempt_id, _ = seed_api_context(
        sessions, invocation_started_at=invocation
    )

    response = client.post(assessment_url(execution_id, attempt_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution"]["status"] == execution_status
    assert body["attempt"]["status"] == attempt_status
    assert body["attempt"]["failure_cause"] == "process_interrupted"
    assert body["attempt"]["outcome_certainty"] == certainty


def test_young_attempt_returns_not_stale_without_mutation(
    recovery_api_context,
) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(
        sessions, claimed_at=datetime.now(timezone.utc)
    )

    response = client.post(assessment_url(execution_id, attempt_id))
    execution, attempts, events = recovery_state(sessions, execution_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_attempt_not_stale"
    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempts[0].status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


def test_duplicate_http_assessment_is_idempotent(recovery_api_context) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(sessions)
    url = assessment_url(execution_id, attempt_id)

    first = client.post(url)
    second = client.post(url)
    _, attempts, events = recovery_state(sessions, execution_id)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["execution"]["completed_at"] == second.json()["execution"][
        "completed_at"
    ]
    assert first.json()["attempt"]["completed_at"] == second.json()["attempt"][
        "completed_at"
    ]
    assert len(attempts) == 1
    assert len(events) == 1


def test_missing_execution_and_attempt_are_404(recovery_api_context) -> None:
    client, sessions = recovery_api_context
    execution_id, _, _ = seed_api_context(sessions)

    missing_execution = client.post(assessment_url(999, 999))
    missing_attempt = client.post(assessment_url(execution_id, 999))

    assert missing_execution.status_code == 404
    assert missing_execution.json()["detail"]["code"] == (
        "action_execution_not_found"
    )
    assert missing_attempt.status_code == 404
    assert missing_attempt.json()["detail"]["code"] == "execution_attempt_not_found"


def test_attempt_mismatch_is_409(recovery_api_context) -> None:
    client, sessions = recovery_api_context
    execution_id, _, _ = seed_api_context(sessions)
    _, other_attempt_id, _ = seed_api_context(sessions)

    response = client.post(assessment_url(execution_id, other_attempt_id))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_attempt_mismatch"


def test_noncanonical_attempt_is_409(recovery_api_context) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(sessions)
    with sessions() as session:
        session.add(
            ActionExecutionAttemptRecord(
                execution_id=execution_id,
                attempt_number=2,
                status=ActionExecutionAttemptStatus.FAILED,
                claimed_at=stale_time(),
                completed_at=stale_time(),
                error={"code": "legacy_test"},
                failure_cause=FailureCause.LEGACY_UNCLASSIFIED,
                outcome_certainty=OutcomeCertainty.LEGACY_UNDETERMINED,
            )
        )
        session.commit()

    response = client.post(assessment_url(execution_id, attempt_id))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_attempt_not_canonical"


@pytest.mark.parametrize(
    ("execution_status", "attempt_status", "failure_cause", "certainty", "result"),
    [
        (
            ActionExecutionStatus.COMPLETED,
            ActionExecutionAttemptStatus.COMPLETED,
            None,
            OutcomeCertainty.APPLIED_ACKNOWLEDGED,
            {"success": True},
        ),
        (
            ActionExecutionStatus.FAILED,
            ActionExecutionAttemptStatus.FAILED,
            FailureCause.TOOL_REJECTED,
            OutcomeCertainty.NOT_APPLIED,
            None,
        ),
        (
            ActionExecutionStatus.OUTCOME_UNKNOWN,
            ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            FailureCause.TOOL_EXCEPTION,
            OutcomeCertainty.UNKNOWN,
            None,
        ),
    ],
)
def test_other_terminal_classifications_return_conflict_without_rewrite(
    recovery_api_context,
    execution_status,
    attempt_status,
    failure_cause,
    certainty,
    result,
) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(
        sessions,
        invocation_started_at=stale_time(),
        execution_status=execution_status,
        attempt_status=attempt_status,
        failure_cause=failure_cause,
        outcome_certainty=certainty,
        result=result,
    )

    response = client.post(assessment_url(execution_id, attempt_id))
    execution, attempts, events = recovery_state(sessions, execution_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "execution_attempt_already_terminal"
    )
    assert execution.status == execution_status
    assert execution.result == result
    assert attempts[0].failure_cause == failure_cause
    assert attempts[0].outcome_certainty == certainty
    assert events == []


def test_inconsistent_recovery_state_is_409(recovery_api_context) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(
        sessions,
        execution_status=ActionExecutionStatus.COMPLETED,
        attempt_status=ActionExecutionAttemptStatus.RUNNING,
        result={"success": True},
    )

    response = client.post(assessment_url(execution_id, attempt_id))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_recovery_conflict"


def test_request_body_cannot_control_recovery_classification(
    recovery_api_context,
) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(sessions)

    response = client.post(
        assessment_url(execution_id, attempt_id),
        json={
            "cutoff": "2099-01-01T00:00:00Z",
            "desired_status": "completed",
            "failure_cause": "tool_rejected",
            "outcome_certainty": "applied_acknowledged",
            "retry": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["execution"]["status"] == "failed"
    assert response.json()["attempt"]["failure_cause"] == "process_interrupted"
    assert response.json()["attempt"]["outcome_certainty"] == "not_applied"


def test_route_resolves_no_operational_dependencies(
    recovery_api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, _ = seed_api_context(sessions)

    def forbidden(*args, **kwargs):
        raise AssertionError("operational dependency must not be resolved")

    app.dependency_overrides[get_controlled_tools] = forbidden
    app.dependency_overrides[get_responses_gateway] = forbidden
    app.dependency_overrides[get_agents_sdk_model] = forbidden
    monkeypatch.setattr(ActionTools, "restart_simulated_service", forbidden)
    monkeypatch.setattr(InvestigationToolRegistry, "execute", forbidden)
    monkeypatch.setattr(MonitoringTools, "get_application_health", forbidden)
    monkeypatch.setattr(ResponsesGateway, "create_initial", forbidden)

    response = client.post(assessment_url(execution_id, attempt_id))

    assert response.status_code == 200


def test_execute_after_stale_classification_returns_canonical_without_attempt_two(
    recovery_api_context, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, sessions = recovery_api_context
    execution_id, attempt_id, investigation_id = seed_api_context(sessions)
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        incident_id = execution.incident_id
        proposal_id = execution.proposal_id

    calls = 0

    def forbidden(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("capability must not be reinvoked")

    monkeypatch.setattr(InvestigationToolRegistry, "execute", forbidden)
    assessed = client.post(assessment_url(execution_id, attempt_id))
    repeated_execute = client.post(
        f"/incidents/{incident_id}/investigation-runs/{investigation_id}"
        f"/action-proposals/{proposal_id}/execute"
    )
    _, attempts, events = recovery_state(sessions, execution_id)

    assert assessed.status_code == repeated_execute.status_code == 200
    assert repeated_execute.json()["status"] == "failed"
    assert calls == 0
    assert len(attempts) == 1
    assert len(events) == 1
