from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

from api.dependencies import get_controlled_tools
from main import app
from services.tool_registry import InvestigationToolRegistry
from simulation.repository import SimulationRepository
from tools.monitoring import MonitoringTools
from tools.actions import ActionTools
from repositories.investigation_repository import InvestigationRepository

from tests.test_action_execution import create_proposal, execute_url
from tests.test_action_proposals import proposal_url


def completed_execution(client: TestClient) -> tuple[int, int, dict]:
    investigation_id, proposal = create_proposal(client)
    client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    execution = client.post(execute_url(investigation_id, proposal["id"])).json()
    assert execution["status"] == "completed"
    return investigation_id, proposal["id"], execution


def test_completed_execution_is_independently_verified_and_idempotent(
    seeded_client: TestClient, monkeypatch
) -> None:
    investigation_id, proposal_id, execution = completed_execution(seeded_client)
    calls = 0
    original = MonitoringTools.get_application_health

    def counted(self, application_id):
        nonlocal calls
        calls += 1
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", counted)
    url = f"/action-executions/{execution['id']}/verify"
    first = seeded_client.post(
        url, json={"target": "OTHER", "observer": "run_arbitrary_command"}
    )
    second = seeded_client.post(url)
    hydrated = seeded_client.get(f"{url.rsplit('/', 1)[0]}/verification")
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "verified"
    assert first.json()["proposal_id"] == proposal_id
    assert first.json()["expected_outcome"] == {"state": "healthy"}
    assert first.json()["observed_outcome"] == {"state": "healthy"}
    assert first.json()["evidence"] == {
        "target": "SUPPORT-API",
        "observer": "get_application_health",
        "expected_state": "healthy",
        "observed_state": "healthy",
    }
    assert second.json() == first.json() == hydrated.json()
    assert calls == 1
    assert [item["event_type"] for item in events[-3:]] == [
        "verification_requested",
        "verification_started",
        "verification_verified",
    ]
    assert events[-1]["metadata"]["execution_id"] == execution["id"]


def test_completed_execution_can_be_not_verified_without_rewriting_history(
    seeded_client: TestClient,
) -> None:
    repository = SimulationRepository(
        restart_outcomes={"SUPPORT-API": "degraded"}
    )
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(repository)
    investigation_id, proposal_id, execution = completed_execution(seeded_client)

    verification = seeded_client.post(
        f"/action-executions/{execution['id']}/verify"
    ).json()
    proposals = seeded_client.get(proposal_url(investigation_id)).json()
    incident = seeded_client.get("/incidents/INC-023").json()

    assert verification["status"] == "not_verified"
    assert verification["observed_outcome"] == {"state": "degraded"}
    assert verification["error"] is None
    assert execution["status"] == "completed"
    assert proposals[0]["id"] == proposal_id
    assert proposals[0]["approval_status"] == "approved"
    assert incident["status"] != "resolved"


def test_observer_failure_is_distinct_and_safe(seeded_client: TestClient) -> None:
    repository = SimulationRepository(
        unobservable_applications={"SUPPORT-API"}
    )
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(repository)
    investigation_id, _, execution = completed_execution(seeded_client)

    verification = seeded_client.post(
        f"/action-executions/{execution['id']}/verify"
    ).json()
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert verification["status"] == "failed"
    assert verification["observed_outcome"] is None
    assert verification["evidence"] is None
    assert verification["error"] == {
        "code": "observer_failure",
        "message": "Unable to collect reliable post-execution evidence",
    }
    assert events[-1]["event_type"] == "verification_failed"


def test_failed_and_missing_executions_cannot_be_verified(
    seeded_client: TestClient,
) -> None:
    investigation_id, proposal = create_proposal(seeded_client, target="UNKNOWN-APP")
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    failed = seeded_client.post(execute_url(investigation_id, proposal["id"])).json()

    rejected = seeded_client.post(f"/action-executions/{failed['id']}/verify")
    missing = seeded_client.post("/action-executions/999999/verify")

    assert failed["status"] == "failed"
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "execution_not_completed"
    assert missing.status_code == 404


def test_outcome_unknown_execution_cannot_use_normal_verification(
    seeded_client: TestClient, monkeypatch
) -> None:
    investigation_id, proposal = create_proposal(seeded_client)
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    observer_calls = 0

    def ambiguous_execution(self, target, service_name):
        raise RuntimeError("simulated acknowledgement loss")

    def counted_observer(self, application_id):
        nonlocal observer_calls
        observer_calls += 1
        raise AssertionError("normal verification observer must not run")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", ambiguous_execution)
    monkeypatch.setattr(MonitoringTools, "get_application_health", counted_observer)
    execution = seeded_client.post(
        execute_url(investigation_id, proposal["id"])
    ).json()

    response = seeded_client.post(f"/action-executions/{execution['id']}/verify")

    assert execution["status"] == "outcome_unknown"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_not_completed"
    assert observer_calls == 0


def test_running_execution_cannot_be_verified(seeded_client: TestClient, monkeypatch) -> None:
    investigation_id, proposal = create_proposal(seeded_client)
    seeded_client.post(f"{proposal_url(investigation_id)}/{proposal['id']}/approve")
    entered = Event()
    release = Event()
    original = ActionTools.restart_simulated_service

    def paused(self, target, service_name):
        entered.set()
        assert release.wait(timeout=5)
        return original(self, target, service_name)

    monkeypatch.setattr(ActionTools, "restart_simulated_service", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        execution_future = pool.submit(
            seeded_client.post, execute_url(investigation_id, proposal["id"])
        )
        assert entered.wait(timeout=5)
        events = seeded_client.get(
            f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
        ).json()
        execution_id = events[-1]["metadata"]["execution_id"]
        response = seeded_client.post(f"/action-executions/{execution_id}/verify")
        release.set()
        completed = execution_future.result(timeout=5)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "execution_not_completed"
    assert completed.json()["status"] == "completed"


def test_duplicate_requests_observe_only_once(seeded_client: TestClient, monkeypatch) -> None:
    _, _, execution = completed_execution(seeded_client)
    calls = 0
    original = MonitoringTools.get_application_health

    def counted(self, application_id):
        nonlocal calls
        calls += 1
        return original(self, application_id)

    monkeypatch.setattr(MonitoringTools, "get_application_health", counted)
    url = f"/action-executions/{execution['id']}/verify"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: seeded_client.post(url), range(2)))

    assert all(response.status_code == 200 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert calls == 1


def test_terminal_state_and_event_roll_back_together(
    seeded_client: TestClient, monkeypatch
) -> None:
    investigation_id, _, execution = completed_execution(seeded_client)
    original = InvestigationRepository.record_event

    def fail_terminal(self, investigation_id_arg, runtime, event_type, sequence, **kwargs):
        if event_type.value == "verification_verified":
            raise RuntimeError("simulated event write failure")
        return original(
            self, investigation_id_arg, runtime, event_type, sequence, **kwargs
        )

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_terminal)
    try:
        seeded_client.post(f"/action-executions/{execution['id']}/verify")
        raise AssertionError("terminal event failure did not propagate")
    except RuntimeError as error:
        assert str(error) == "simulated event write failure"

    stored = seeded_client.get(
        f"/action-executions/{execution['id']}/verification"
    ).json()
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()
    assert stored["status"] == "running"
    assert events[-1]["event_type"] == "verification_started"
