from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from api.dependencies import get_controlled_tools
from domain.outcome_verification import OutcomeVerificationStatus
from main import app
from repositories.investigation_repository import InvestigationRepository
from repositories.outcome_verification_repository import OutcomeVerificationRepository
from services.tool_registry import InvestigationToolRegistry
from simulation.repository import SimulationRepository

from tests.test_action_execution import create_proposal, execute_url
from tests.test_action_proposals import proposal_url


def create_verification(
    client: TestClient,
    *,
    outcome: str = "healthy",
    observer_failure: bool = False,
) -> tuple[int, dict, dict, dict]:
    repository = SimulationRepository(
        restart_outcomes={"SUPPORT-API": outcome},
        unobservable_applications={"SUPPORT-API"} if observer_failure else (),
    )
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(
        repository
    )
    investigation_id, proposal = create_proposal(client)
    client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    execution = client.post(execute_url(investigation_id, proposal["id"])).json()
    verification = client.post(
        f"/action-executions/{execution['id']}/verify"
    ).json()
    return investigation_id, proposal, execution, verification


def resolution_url(incident: str = "INC-023") -> str:
    return f"/incidents/{incident}/resolution-decisions"


def test_verified_outcome_requires_explicit_human_resolve_and_preserves_history(
    seeded_client: TestClient,
) -> None:
    investigation_id, proposal, execution, verification = create_verification(
        seeded_client
    )
    before = seeded_client.get("/incidents/INC-023").json()
    payload = {
        "verification_id": verification["id"],
        "decision": "resolve",
        "reason": "Post-execution verification confirms service recovery.",
    }

    response = seeded_client.post(resolution_url(), json=payload)
    incident = seeded_client.get("/incidents/INC-023").json()
    decisions = seeded_client.get(resolution_url()).json()
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()
    proposals = seeded_client.get(proposal_url(investigation_id)).json()
    persisted_verification = seeded_client.get(
        f"/action-executions/{execution['id']}/verification"
    ).json()

    assert before["status"] != "resolved"
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "resolve"
    assert incident["status"] == "resolved"
    assert decisions == [response.json()]
    assert [item["event_type"] for item in events[-2:]] == [
        "resolution_reviewed",
        "incident_resolved",
    ]
    assert events[-1]["metadata"] == {
        "incident_id": incident["id"],
        "verification_id": verification["id"],
        "execution_id": execution["id"],
        "proposal_id": proposal["id"],
        "resolution_decision_id": response.json()["id"],
        "decision": "resolve",
    }
    assert proposals[0]["approval_status"] == "approved"
    assert execution["status"] == "completed"
    assert persisted_verification["status"] == "verified"


def test_keep_open_is_historical_and_does_not_resolve(seeded_client: TestClient) -> None:
    _, _, _, verification = create_verification(seeded_client)
    response = seeded_client.post(
        resolution_url(),
        json={
            "verification_id": verification["id"],
            "decision": "keep_open",
            "reason": "Need additional stability observation.",
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"] == "keep_open"
    assert seeded_client.get("/incidents/INC-023").json()["status"] != "resolved"
    assert seeded_client.get(resolution_url()).json() == [response.json()]


def test_resolve_rejects_running_not_verified_and_failed_outcomes(
    seeded_client: TestClient, monkeypatch
) -> None:
    original_finish = OutcomeVerificationRepository.finish

    def leave_running(self, execution, proposal, record, runtime, *args, **kwargs):
        return record

    monkeypatch.setattr(OutcomeVerificationRepository, "finish", leave_running)
    _, _, _, running = create_verification(seeded_client)
    running_response = seeded_client.post(
        resolution_url(),
        json={"verification_id": running["id"], "decision": "resolve"},
    )
    running_keep_open = seeded_client.post(
        resolution_url(),
        json={"verification_id": running["id"], "decision": "keep_open"},
    )
    monkeypatch.setattr(OutcomeVerificationRepository, "finish", original_finish)

    # Separate clients normally isolate these scenarios; here distinct incidents are not
    # required because rejected reviews never mutate the incident.
    repository = SimulationRepository(restart_outcomes={"SUPPORT-API": "degraded"})
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(repository)
    investigation_id, proposal = create_proposal(seeded_client)
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    execution = seeded_client.post(execute_url(investigation_id, proposal["id"])).json()
    not_verified = seeded_client.post(f"/action-executions/{execution['id']}/verify").json()
    not_verified_response = seeded_client.post(
        resolution_url(),
        json={"verification_id": not_verified["id"], "decision": "resolve"},
    )

    repository = SimulationRepository(unobservable_applications={"SUPPORT-API"})
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(repository)
    investigation_id, proposal = create_proposal(seeded_client)
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    execution = seeded_client.post(execute_url(investigation_id, proposal["id"])).json()
    failed = seeded_client.post(f"/action-executions/{execution['id']}/verify").json()
    failed_response = seeded_client.post(
        resolution_url(),
        json={"verification_id": failed["id"], "decision": "resolve"},
    )

    assert running["status"] == "running"
    assert not_verified["status"] == "not_verified"
    assert failed["status"] == "failed"
    assert {
        running_response.status_code,
        running_keep_open.status_code,
        not_verified_response.status_code,
        failed_response.status_code,
    } == {409}
    assert seeded_client.get("/incidents/INC-023").json()["status"] != "resolved"


def test_verification_ownership_and_client_authority_are_enforced(
    seeded_client: TestClient,
) -> None:
    _, _, _, verification = create_verification(seeded_client)
    cross_incident = seeded_client.post(
        resolution_url("INC-001"),
        json={"verification_id": verification["id"], "decision": "resolve"},
    )
    missing = seeded_client.post(
        resolution_url(), json={"verification_id": 999999, "decision": "resolve"}
    )
    spoofed = seeded_client.post(
        resolution_url(),
        json={
            "verification_id": verification["id"],
            "decision": "resolve",
            "verification_status": "verified",
            "execution_result": {"success": True},
            "proposal_id": 999999,
        },
    )

    assert cross_incident.status_code == 409
    assert missing.status_code == 404
    assert spoofed.status_code == 422


def test_generic_incident_api_cannot_bypass_resolution_gate(
    seeded_client: TestClient,
) -> None:
    patch_response = seeded_client.patch(
        "/incidents/INC-023", json={"status": "resolved"}
    )
    create_response = seeded_client.post(
        "/incidents",
        json={
            "title": "Injected status",
            "description": "Attempt direct resolution.",
            "category": "test",
            "priority": "low",
            "requester": "operator",
            "status": "resolved",
        },
    )

    assert patch_response.status_code == 405
    assert create_response.status_code == 422
    assert seeded_client.get("/incidents/INC-023").json()["status"] != "resolved"


def test_duplicate_and_concurrent_resolve_is_canonical(
    seeded_client: TestClient,
) -> None:
    investigation_id, _, _, verification = create_verification(seeded_client)
    payload = {"verification_id": verification["id"], "decision": "resolve"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(lambda _: seeded_client.post(resolution_url(), json=payload), range(2))
        )
    repeated = seeded_client.post(resolution_url(), json=payload)
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"] == repeated.json()["id"]
    assert len(seeded_client.get(resolution_url()).json()) == 1
    assert sum(item["event_type"] == "incident_resolved" for item in events) == 1


def test_resolution_state_decision_and_events_roll_back_together(
    seeded_client: TestClient, monkeypatch
) -> None:
    investigation_id, _, _, verification = create_verification(seeded_client)
    original = InvestigationRepository.record_event

    def fail_resolved(self, investigation_id_arg, runtime, event_type, sequence, **kwargs):
        if event_type.value == "incident_resolved":
            raise RuntimeError("simulated resolution event failure")
        return original(self, investigation_id_arg, runtime, event_type, sequence, **kwargs)

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_resolved)
    try:
        seeded_client.post(
            resolution_url(),
            json={"verification_id": verification["id"], "decision": "resolve"},
        )
        raise AssertionError("resolution event failure did not propagate")
    except RuntimeError as error:
        assert str(error) == "simulated resolution event failure"

    assert seeded_client.get("/incidents/INC-023").json()["status"] != "resolved"
    assert seeded_client.get(resolution_url()).json() == []
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()
    assert all(item["event_type"] != "resolution_reviewed" for item in events)
