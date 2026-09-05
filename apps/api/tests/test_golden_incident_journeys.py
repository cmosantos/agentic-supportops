from collections.abc import Callable

from fastapi.testclient import TestClient

from api.dependencies import get_controlled_tools, get_responses_gateway
from domain.ai import AIInvestigationResult, AIInvestigationStatus
from main import app
from services.tool_registry import InvestigationToolRegistry
from simulation.repository import SimulationRepository
from tests.fakes import FakeResponsesGateway, call_turn, final_turn


def _assessment(action_type: str, target: str, parameters: dict[str, str]) -> str:
    return AIInvestigationResult(
        status=AIInvestigationStatus.COMPLETED,
        summary="The incident was investigated using persisted read-only evidence.",
        diagnosis="The observed resource is degraded in the Contoso simulation.",
        confidence=0.92,
        supporting_evidence=["The governed observer returned the degraded state."],
        evidence_ids=[],
        recommended_next_steps=["Review the bounded remediation with an operator."],
        missing_information=[],
        human_action_required=True,
        # The operator proposal is created after the run, with the actual
        # persisted evidence ID returned by the application. The model result
        # therefore does not invent an evidence identifier here.
        proposed_action=None,
    ).model_dump_json()


def _proposal_payload(action_type: str, target: str, parameters: dict[str, str], evidence_id: int) -> dict:
    return {
        "action_type": action_type,
        "target": target,
        "parameters": parameters,
        "rationale": "The persisted read-only evidence supports this bounded remediation.",
        "supporting_evidence_ids": [evidence_id],
        "risk_level": "medium",
    }


def _run_golden_journey(
    client: TestClient,
    *,
    catalog_id: str,
    tool_name: str,
    tool_arguments: dict[str, str],
    action_type: str,
    target: str,
    parameters: dict[str, str],
    initial_state: Callable[[SimulationRepository], object],
    changed_state: Callable[[SimulationRepository], object],
) -> None:
    repository = SimulationRepository()
    previous_tools = app.dependency_overrides.get(get_controlled_tools)
    previous_gateway = app.dependency_overrides.get(get_responses_gateway)
    gateway = FakeResponsesGateway(
        [
            call_turn("golden-tools", ("observed", tool_name, tool_arguments)),
            final_turn("golden-final", _assessment(action_type, target, parameters)),
        ]
    )
    app.dependency_overrides[get_controlled_tools] = lambda: InvestigationToolRegistry(repository)
    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    try:
        investigation_response = client.post(f"/incidents/{catalog_id}/investigate-ai")
        assert investigation_response.status_code == 200, investigation_response.text
        investigation = investigation_response.json()
        run_id = investigation["investigation"]["id"]
        evidence_id = investigation["evidence"][0]["id"]
        assert investigation["investigation"]["status"] == "completed"
        assert investigation["investigation"]["mode"] == "ai"

        proposal_url = f"/incidents/{catalog_id}/investigation-runs/{run_id}/action-proposals"
        proposal_response = client.post(
            proposal_url,
            json=_proposal_payload(action_type, target, parameters, evidence_id),
        )
        assert proposal_response.status_code == 201, proposal_response.text
        proposal = proposal_response.json()
        assert proposal["supporting_evidence_ids"] == [evidence_id]

        before = initial_state(repository)
        blocked_execution = client.post(f"{proposal_url}/{proposal['id']}/execute")
        assert blocked_execution.status_code == 409
        assert initial_state(repository) == before

        approved = client.post(f"{proposal_url}/{proposal['id']}/approve")
        assert approved.status_code == 200
        assert initial_state(repository) == before

        execution_response = client.post(f"{proposal_url}/{proposal['id']}/execute")
        assert execution_response.status_code == 200, execution_response.text
        execution = execution_response.json()
        assert execution["status"] == "completed"
        assert execution["completion_basis"] == "acknowledged_result"
        assert changed_state(repository) == initial_state(repository)
        assert changed_state(repository) != before

        attempt = client.get(f"/action-executions/{execution['id']}/attempt")
        assert attempt.status_code == 200
        assert attempt.json()["outcome_certainty"] == "applied_acknowledged"
        assert client.get(
            f"/action-executions/{execution['id']}/attempts/{attempt.json()['id']}/reconciliation"
        ).status_code == 404

        verification_response = client.post(f"/action-executions/{execution['id']}/verify")
        assert verification_response.status_code == 200, verification_response.text
        verification = verification_response.json()
        assert verification["status"] == "verified"
        expected_state = "false" if action_type == "unlock_simulated_user" else "healthy"
        assert verification["observed_outcome"] == {"state": expected_state}

        assert client.get(f"/incidents/{catalog_id}").json()["status"] != "resolved"
        resolution = client.post(
            f"/incidents/{catalog_id}/resolution-decisions",
            json={
                "verification_id": verification["id"],
                "decision": "resolve",
                "reason": "Operator reviewed the independent verification.",
            },
        )
        assert resolution.status_code == 200, resolution.text
        assert client.get(f"/incidents/{catalog_id}").json()["status"] == "resolved"
    finally:
        if previous_tools is None:
            app.dependency_overrides.pop(get_controlled_tools, None)
        else:
            app.dependency_overrides[get_controlled_tools] = previous_tools
        if previous_gateway is None:
            app.dependency_overrides.pop(get_responses_gateway, None)
        else:
            app.dependency_overrides[get_responses_gateway] = previous_gateway


def test_api_degradation_golden_journey(seeded_client: TestClient) -> None:
    _run_golden_journey(
        seeded_client,
        catalog_id="INC-023",
        tool_name="get_application_health",
        tool_arguments={"application_id": "SUPPORT-API"},
        action_type="restart_simulated_service",
        target="SUPPORT-API",
        parameters={"service_name": "SupportApi"},
        initial_state=lambda repository: repository.get_application("SUPPORT-API").status,
        changed_state=lambda repository: repository.get_application("SUPPORT-API").status,
    )


def test_database_exhaustion_golden_journey(seeded_client: TestClient) -> None:
    _run_golden_journey(
        seeded_client,
        catalog_id="INC-024",
        tool_name="get_application_health",
        tool_arguments={"application_id": "CONTOSO-DB"},
        action_type="reset_simulated_application_state",
        target="CONTOSO-DB",
        parameters={},
        initial_state=lambda repository: repository.get_application("CONTOSO-DB").status,
        changed_state=lambda repository: repository.get_application("CONTOSO-DB").status,
    )


def test_locked_identity_golden_journey(seeded_client: TestClient) -> None:
    _run_golden_journey(
        seeded_client,
        catalog_id="INC-026",
        tool_name="get_account_status",
        tool_arguments={"user_id": "USR-FRANK"},
        action_type="unlock_simulated_user",
        target="USR-FRANK",
        parameters={},
        initial_state=lambda repository: repository.get_user("USR-FRANK").account.locked,
        changed_state=lambda repository: repository.get_user("USR-FRANK").account.locked,
    )


def test_deterministic_golden_playbooks_persist_run_scoped_evidence(
    seeded_client: TestClient,
) -> None:
    database = seeded_client.post("/incidents/INC-024/investigate")
    identity = seeded_client.post("/incidents/INC-026/investigate")

    assert database.status_code == 200, database.text
    assert identity.status_code == 200, identity.text
    assert all(item["origin"] == "deterministic" for item in database.json()["evidence"])
    assert all(item["origin"] == "deterministic" for item in identity.json()["evidence"])
    assert seeded_client.get("/incidents/INC-024/evidence").json() == database.json()["evidence"]
    assert seeded_client.get("/incidents/INC-026/evidence").json() == identity.json()["evidence"]
    database_ids = {item["id"] for item in database.json()["evidence"]}
    identity_ids = {item["id"] for item in identity.json()["evidence"]}
    assert database_ids.isdisjoint(identity_ids)
    assert all(item["incident_id"] != identity.json()["incident_id"] for item in database.json()["evidence"])
    assert all(item["incident_id"] != database.json()["incident_id"] for item in identity.json()["evidence"])
