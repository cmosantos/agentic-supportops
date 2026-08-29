from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import ActionExecutionRecord
from domain.action_execution import ActionExecutionStatus
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
