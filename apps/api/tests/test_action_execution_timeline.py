from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from db.models import (
    ActionExecutionRecord,
    ActionProposalRecord,
    InvestigationEventRecord,
)
from domain.ai import InvestigationRuntime
from repositories.action_execution_repository import ActionExecutionRepository
from tests.test_action_execution import (
    approved_execution,
    execution_context,
    execute_url,
    persisted_execution_state,
)
from tools.actions import ActionTools


def timeline_url(execution_id: int) -> str:
    return f"/action-executions/{execution_id}/timeline"


def test_timeline_is_chronological_scoped_and_uses_canonical_attempt(
    execution_context,
) -> None:
    client, sessions, _ = execution_context
    first_investigation, first_proposal = approved_execution(client)
    first = client.post(execute_url(first_investigation, first_proposal)).json()
    second_investigation, second_proposal = approved_execution(client)
    second = client.post(execute_url(second_investigation, second_proposal)).json()
    _, attempts, _ = persisted_execution_state(sessions)
    first_attempt = next(item for item in attempts if item.execution_id == first["id"])

    response = client.get(timeline_url(first["id"]))

    assert response.status_code == 200, response.text
    entries = response.json()
    assert [entry["timestamp"] for entry in entries] == sorted(
        entry["timestamp"] for entry in entries
    )
    assert {entry["execution_id"] for entry in entries} == {first["id"]}
    assert second["id"] not in {entry["execution_id"] for entry in entries}
    assert [entry["event_type"] for entry in entries] == [
        "execution_requested",
        "execution_started",
        "execution_completed",
    ]
    assert {entry["attempt_id"] for entry in entries[:2]} == {first_attempt.id}


def test_timeline_empty_state_and_missing_execution(execution_context) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    execution = client.post(execute_url(investigation_id, proposal_id)).json()
    with sessions() as session:
        session.execute(delete(InvestigationEventRecord))
        session.commit()

    response = client.get(timeline_url(execution["id"]))
    missing = client.get(timeline_url(999999))

    assert response.status_code == 200
    assert response.json() == []
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "action_execution_not_found"


def test_timeline_get_is_read_only(execution_context) -> None:
    client, sessions, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    execution = client.post(execute_url(investigation_id, proposal_id)).json()

    with sessions() as session:
        before = (
            session.scalar(select(func.count()).select_from(InvestigationEventRecord)),
            session.get(ActionExecutionRecord, execution["id"]).status,
        )
    first = client.get(timeline_url(execution["id"]))
    second = client.get(timeline_url(execution["id"]))
    with sessions() as session:
        after = (
            session.scalar(select(func.count()).select_from(InvestigationEventRecord)),
            session.get(ActionExecutionRecord, execution["id"]).status,
        )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert after == before


def test_timeline_projects_stale_assessment_reason(execution_context) -> None:
    client, sessions, _ = execution_context
    _, proposal_id = approved_execution(client)
    with sessions() as session:
        proposal = session.get(ActionProposalRecord, proposal_id)
        execution, attempt, created = ActionExecutionRepository(session).start(
            proposal, InvestigationRuntime.MANUAL_RESPONSES
        )
        assert created and attempt is not None
        attempt.claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        session.commit()
        execution_id, attempt_id = execution.id, attempt.id

    assessed = client.post(
        f"/action-executions/{execution_id}/attempts/{attempt_id}/stale-assessment"
    )
    entries = client.get(timeline_url(execution_id)).json()

    assert assessed.status_code == 200, assessed.text
    assert entries[-1]["event_type"] == "execution_attempt_interruption_assessed"
    assert entries[-1]["attempt_id"] == attempt_id
    assert entries[-1]["reason"] == "stale_before_invocation"


def test_timeline_projects_unknown_outcome_and_reconciliation(
    execution_context, monkeypatch
) -> None:
    client, _, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)

    def timeout(self, target, service_name):
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", timeout)
    execution = client.post(execute_url(investigation_id, proposal_id)).json()
    attempt = client.get(f"/action-executions/{execution['id']}/attempt").json()
    monkeypatch.undo()
    reconciled = client.post(
        f"/action-executions/{execution['id']}/attempts/{attempt['id']}/reconcile"
    )
    entries = client.get(timeline_url(execution["id"])).json()

    assert reconciled.status_code == 200, reconciled.text
    event_types = [entry["event_type"] for entry in entries]
    for event_type in (
        "execution_attempt_outcome_unknown",
        "reconciliation_requested",
        "reconciliation_started",
        "reconciliation_undesired_state_observed",
    ):
        assert event_type in event_types
    assert "execution_completed" not in event_types
    assert all(
        entry["attempt_id"] == attempt["id"]
        for entry in entries
        if entry["event_type"].startswith("reconciliation_")
    )


def test_timeline_projects_verification_and_human_resolution(
    execution_context,
) -> None:
    client, _, _ = execution_context
    investigation_id, proposal_id = approved_execution(client)
    execution = client.post(execute_url(investigation_id, proposal_id)).json()
    verification = client.post(
        f"/action-executions/{execution['id']}/verify"
    ).json()

    resolved = client.post(
        f"/incidents/{execution['incident_id']}/resolution-decisions",
        json={
            "verification_id": verification["id"],
            "decision": "resolve",
            "reason": "Verified technical recovery reviewed by operator.",
        },
    )
    entries = client.get(timeline_url(execution["id"])).json()
    event_types = [entry["event_type"] for entry in entries]

    assert resolved.status_code == 200, resolved.text
    expected = [
        "verification_requested",
        "verification_started",
        "verification_verified",
        "resolution_reviewed",
        "incident_resolved",
    ]
    positions = [event_types.index(event_type) for event_type in expected]
    assert positions == sorted(positions)
    assert next(
        entry for entry in entries if entry["event_type"] == "resolution_reviewed"
    )["reason"] == "resolve"
