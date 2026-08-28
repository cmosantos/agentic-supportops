import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_responses_gateway
from db.base import Base
from db.models import ActionProposalRecord, IncidentRecord
from domain.action_proposal import ActionProposalCreate, ApprovalStatus
from domain.ai import (
    AIInvestigationResult,
    AIInvestigationStatus,
    InvestigationEventType,
    InvestigationRuntime,
    ProviderUsage,
)
from domain.investigation import InvestigationOrigin
from main import app
from repositories.action_proposal_repository import ActionProposalRepository
from repositories.investigation_repository import InvestigationRepository
from services.action_proposal_service import ActionProposalService
from services.tool_registry import InvestigationToolRegistry
from simulation.seed import seed_catalog
from tests.fakes import FakeResponsesGateway, call_turn, final_turn


def run_actionable_investigation(client: TestClient) -> dict:
    gateway = FakeResponsesGateway(
        [
            call_turn(
                "proposal-tools",
                (
                    "application-health",
                    "get_application_health",
                    {"application_id": "SUPPORT-API"},
                ),
            ),
            final_turn("proposal-final"),
        ]
    )
    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    try:
        response = client.post("/incidents/INC-023/investigate-ai")
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)
    assert response.status_code == 200, response.text
    return response.json()


def proposal_payload(evidence_id: int, action_type="reset_simulated_application_state"):
    return {
        "action_type": action_type,
        "target": "SUPPORT-API",
        "parameters": {},
        "rationale": "Reset may restore the simulated application after operator review.",
        "supporting_evidence_ids": [evidence_id],
        "risk_level": "medium",
    }


def proposal_url(investigation_id: int) -> str:
    return f"/incidents/INC-023/investigation-runs/{investigation_id}/action-proposals"


def test_valid_proposal_is_pending_audited_and_historical(
    seeded_client: TestClient,
) -> None:
    execution = run_actionable_investigation(seeded_client)
    investigation_id = execution["investigation"]["id"]
    evidence_id = execution["evidence"][0]["id"]

    created = seeded_client.post(
        proposal_url(investigation_id), json=proposal_payload(evidence_id)
    )
    listed = seeded_client.get(proposal_url(investigation_id))
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert created.status_code == 201, created.text
    assert created.json()["approval_status"] == "pending"
    assert created.json()["investigation_id"] == investigation_id
    assert created.json()["supporting_evidence_ids"] == [evidence_id]
    assert listed.json() == [created.json()]
    assert events[-1]["event_type"] == "action_proposal_created"
    assert events[-1]["metadata"]["proposal_id"] == created.json()["id"]


def test_invalid_action_and_cross_investigation_evidence_are_rejected(
    seeded_client: TestClient,
) -> None:
    first = run_actionable_investigation(seeded_client)
    second = run_actionable_investigation(seeded_client)
    first_evidence = first["evidence"][0]["id"]
    second_investigation = second["investigation"]["id"]

    invalid_action = seeded_client.post(
        proposal_url(second_investigation),
        json=proposal_payload(first_evidence, "run_arbitrary_command"),
    )
    wrong_evidence = seeded_client.post(
        proposal_url(second_investigation), json=proposal_payload(first_evidence)
    )

    assert invalid_action.status_code == 422
    assert invalid_action.json()["detail"]["code"] == "invalid_action_type"
    assert wrong_evidence.status_code == 422
    assert wrong_evidence.json()["detail"]["code"] == "evidence_mismatch"
    assert seeded_client.get(proposal_url(second_investigation)).json() == []


@pytest.mark.parametrize(
    ("decision", "body", "expected_status", "event_type"),
    [
        ("approve", None, "approved", "action_proposal_approved"),
        (
            "reject",
            {"reason": "Operator requires a maintenance window."},
            "rejected",
            "action_proposal_rejected",
        ),
    ],
)
def test_pending_proposal_can_be_decided_once(
    seeded_client: TestClient, decision, body, expected_status, event_type
) -> None:
    execution = run_actionable_investigation(seeded_client)
    investigation_id = execution["investigation"]["id"]
    created = seeded_client.post(
        proposal_url(investigation_id),
        json=proposal_payload(execution["evidence"][0]["id"]),
    ).json()
    url = f"{proposal_url(investigation_id)}/{created['id']}/{decision}"

    decided = seeded_client.post(url, json=body) if body else seeded_client.post(url)
    conflicting = seeded_client.post(
        f"{proposal_url(investigation_id)}/{created['id']}/"
        f"{'reject' if decision == 'approve' else 'approve'}",
        json={"reason": "Conflicting decision"} if decision == "approve" else None,
    )
    events = seeded_client.get(
        f"/incidents/INC-023/investigation-runs/{investigation_id}/events"
    ).json()

    assert decided.status_code == 200
    assert decided.json()["approval_status"] == expected_status
    assert decided.json()["decision_at"] is not None
    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "proposal_already_decided"
    assert events[-1]["event_type"] == event_type


def test_ineligible_investigation_cannot_create_proposal(
    seeded_client: TestClient,
) -> None:
    gateway = FakeResponsesGateway([final_turn()])
    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    try:
        execution = seeded_client.post("/incidents/INC-023/investigate-ai").json()
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)
    investigation_id = execution["investigation"]["id"]

    response = seeded_client.post(
        proposal_url(investigation_id), json=proposal_payload(999)
    )

    assert execution["investigation"]["status"] == "insufficient_evidence"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "investigation_not_eligible"


def test_approval_and_event_roll_back_together_on_commit_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'approval.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(
            select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-023")
        )
        investigations = InvestigationRepository(session)
        run = investigations.start_ai_run(incident.id, "fake")
        evidence = investigations.record_result(
            incident.id,
            InvestigationToolRegistry().execute(
                "get_application_health", {"application_id": "SUPPORT-API"}
            ),
            origin=InvestigationOrigin.AI,
            investigation_id=run.id,
        )
        result = AIInvestigationResult(
            status=AIInvestigationStatus.COMPLETED,
            summary="Application state was inspected.",
            diagnosis="The simulated application is degraded.",
            confidence=0.9,
            supporting_evidence=["Application health is degraded."],
            evidence_ids=[evidence.id],
            recommended_next_steps=["Consider a controlled reset."],
            missing_information=[],
            human_action_required=True,
            proposed_action=None,
        )
        investigations.record_event(
            run.id,
            InvestigationRuntime.MANUAL_RESPONSES,
            InvestigationEventType.RUN_COMPLETED,
            1,
            commit=False,
        )
        investigations.complete_ai_run(run, result, "response", ProviderUsage())
        service = ActionProposalService(
            ActionProposalRepository(session), investigations
        )
        proposal = service.create(
            run,
            ActionProposalCreate.model_validate(proposal_payload(evidence.id)),
        )
        run_id = run.id
        proposal_id = proposal.id

        def fail_commit() -> None:
            raise RuntimeError("simulated decision commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated decision commit failure"):
            ActionProposalRepository(session).decide(
                session.get(ActionProposalRecord, proposal.id),
                ApprovalStatus.APPROVED,
                InvestigationRuntime.MANUAL_RESPONSES,
            )

    with sessions() as verification:
        stored = verification.get(ActionProposalRecord, proposal_id)
        events = InvestigationRepository(verification).list_events(run_id)
        assert stored.approval_status == ApprovalStatus.PENDING
        assert events[-1].event_type == InvestigationEventType.ACTION_PROPOSAL_CREATED
    engine.dispose()


def test_approval_does_not_execute_the_proposed_action(
    seeded_client: TestClient,
) -> None:
    before = InvestigationToolRegistry().execute(
        "get_application_health", {"application_id": "SUPPORT-API"}
    )
    execution = run_actionable_investigation(seeded_client)
    investigation_id = execution["investigation"]["id"]
    proposal = seeded_client.post(
        proposal_url(investigation_id),
        json=proposal_payload(execution["evidence"][0]["id"]),
    ).json()

    approved = seeded_client.post(
        f"{proposal_url(investigation_id)}/{proposal['id']}/approve"
    )
    after = InvestigationToolRegistry().execute(
        "get_application_health", {"application_id": "SUPPORT-API"}
    )

    assert approved.status_code == 200
    assert approved.json()["approval_status"] == "approved"
    assert before == after
