import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.dependencies import get_responses_gateway
from db.base import Base
from db.models import IncidentRecord
from domain.ai import AIInvestigationStatus, ProviderUsage, ResponsesTurn
from integrations.responses_gateway import ResponsesProviderError
from main import app
from repositories.investigation_repository import (
    ActiveInvestigationExistsError,
    InvestigationRepository,
)
from services.ai_investigation_service import AIInvestigationError, AIInvestigationService
from services.tool_registry import InvestigationToolRegistry
from simulation.seed import seed_catalog
from tests.fakes import FakeResponsesGateway, call_turn, final_result, final_turn


def run_with_fake(client: TestClient, incident: str, gateway: FakeResponsesGateway):
    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    try:
        return client.post(f"/incidents/{incident}/investigate-ai")
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)


def test_ai_endpoint_reports_not_configured_without_key(seeded_client: TestClient) -> None:
    app.dependency_overrides[get_responses_gateway] = lambda: None
    try:
        response = seeded_client.post("/incidents/INC-019/investigate-ai")
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ai_not_configured"
    assert seeded_client.get("/health").status_code == 200
    assert seeded_client.post("/incidents/INC-019/investigate").status_code == 200


def test_multiple_calls_preserve_call_ids_and_persist_ai_origin(seeded_client: TestClient) -> None:
    gateway = FakeResponsesGateway(
        [
            call_turn(
                "resp-1",
                ("call-gateway", "check_gateway_connectivity", {"device_id": "WS-003"}),
                ("call-external", "check_external_connectivity", {"device_id": "WS-003"}),
            ),
            call_turn(
                "resp-2",
                ("call-dns", "check_dns_resolution", {"device_id": "WS-003", "hostname": "portal.contoso.example"}),
            ),
            final_turn(),
        ]
    )
    response = run_with_fake(seeded_client, "INC-019", gateway)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item.call_id for item in gateway.continuations[0][1]] == ["call-gateway", "call-external"]
    assert gateway.continuations[1][0] == "resp-2"
    assert all(item["origin"] == "ai" for item in body["steps"])
    assert all(item["origin"] == "ai" for item in body["evidence"])
    assert body["investigation"]["usage"]["total_tokens"] == 36
    assert body["investigation"]["usage"]["response_iterations"] == 3
    stored = seeded_client.get("/incidents/INC-019/ai-investigation")
    assert stored.status_code == 200
    assert stored.json()["investigation"]["response_id"] == "resp-final"


@pytest.mark.parametrize(
    ("incident", "calls", "source", "field", "expected"),
    [
        ("INC-002", [("a", "get_account_status", {"user_id": "USR-BOB"}), ("b", "get_mailbox", {"reference": "MBX-SUPPORT"}), ("c", "get_mailbox_permissions", {"mailbox_id": "MBX-SUPPORT", "user_id": "USR-BOB"})], "get_mailbox_permissions", "automapping", False),
        ("INC-003", [("a", "get_mailbox_permissions", {"mailbox_id": "MBX-SUPPORT", "user_id": "USR-CAROL"})], "get_mailbox_permissions", "send_as", False),
        ("INC-014", [("a", "get_disk_usage", {"device_id": "WS-002"})], "get_disk_usage", "disk_percent", 98.4),
        ("INC-021", [("a", "get_metrics", {"host_id": "APP-02"}), ("b", "get_recent_alerts", {"host_id": "APP-02"})], "get_metrics", "cpu_percent", 97.6),
        ("INC-023", [("a", "get_application_health", {"application_id": "SUPPORT-API"}), ("b", "get_metrics", {"host_id": "API-01"})], "get_application_health", "error_rate_percent", 12.4),
    ],
)
def test_ai_evaluation_workflows_use_real_tools(
    seeded_client: TestClient, incident, calls, source, field, expected
) -> None:
    gateway = FakeResponsesGateway([call_turn("resp-tools", *calls), final_turn()])
    response = run_with_fake(seeded_client, incident, gateway)
    assert response.status_code == 200, response.text
    evidence = next(item for item in response.json()["evidence"] if item["source"] == source)
    assert evidence["payload"][field] == expected
    assert response.json()["investigation"]["usage"]["response_iterations"] == 2


def test_single_turn_final_response_persists_one_iteration(
    seeded_client: TestClient,
) -> None:
    response = run_with_fake(
        seeded_client,
        "INC-019",
        FakeResponsesGateway([final_turn()]),
    )

    assert response.status_code == 200
    assert response.json()["investigation"]["usage"]["response_iterations"] == 1


def test_tool_failure_is_returned_to_model_and_persisted_as_failed_step(seeded_client: TestClient) -> None:
    gateway = FakeResponsesGateway(
        [
            call_turn("resp-bad", ("call-bad", "get_disk_usage", {"device_id": "WS-999"})),
            final_turn(),
        ]
    )
    response = run_with_fake(seeded_client, "INC-014", gateway)
    output = json.loads(gateway.continuations[0][1][0].output)
    assert output["success"] is False
    assert output["error"]["code"] == "resource_not_found"
    assert response.json()["steps"][0]["status"] == "failed"
    assert response.json()["evidence"] == []


def test_malformed_final_result_is_translated_and_persisted(seeded_client: TestClient) -> None:
    gateway = FakeResponsesGateway([final_turn(output='{"status":"completed"}')])
    response = run_with_fake(seeded_client, "INC-019", gateway)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_invalid_result"
    stored = seeded_client.get("/incidents/INC-019/ai-investigation").json()
    assert stored["investigation"]["status"] == "failed"
    assert stored["investigation"]["error"]["code"] == "ai_invalid_result"


def test_unknown_incident_and_provider_failure(seeded_client: TestClient) -> None:
    gateway = FakeResponsesGateway([final_turn()])
    assert run_with_fake(seeded_client, "INC-999", gateway).status_code == 404

    class FailedGateway:
        model = "fake-responses-model"

        def create_initial(self, incident_input: str):
            raise ResponsesProviderError("ai_rate_limited", "OpenAI rate limit reached")

    response = run_with_fake(seeded_client, "INC-019", FailedGateway())
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "ai_rate_limited"


def test_repeated_call_and_tool_limits_stop_cleanly(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'limits.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-014"))
        gateway = FakeResponsesGateway([
            call_turn("r1", ("c1", "get_disk_usage", {"device_id": "WS-002"})),
            call_turn("r2", ("c2", "get_disk_usage", {"device_id": "WS-002"})),
        ])
        service = AIInvestigationService(
            InvestigationRepository(session), InvestigationToolRegistry(), gateway,
            max_response_iterations=5, max_tool_calls=5, max_identical_tool_calls=1,
        )
        with pytest.raises(AIInvestigationError, match="Repeated identical") as error:
            service.investigate(incident)
        assert error.value.code == "ai_repeated_call_limit"
        assert InvestigationRepository(session).get_ai_run(incident.id).status == "failed"
    engine.dispose()


def test_total_tool_call_limit_stops_before_excess_execution(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'tool-limit.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-019"))
        gateway = FakeResponsesGateway([
            call_turn(
                "r1",
                ("c1", "check_gateway_connectivity", {"device_id": "WS-003"}),
                ("c2", "check_external_connectivity", {"device_id": "WS-003"}),
            )
        ])
        service = AIInvestigationService(
            InvestigationRepository(session), InvestigationToolRegistry(), gateway,
            max_response_iterations=5, max_tool_calls=1, max_identical_tool_calls=2,
        )
        with pytest.raises(AIInvestigationError) as error:
            service.investigate(incident)
        assert error.value.code == "ai_tool_limit_reached"
    engine.dispose()


def test_response_iteration_limit_stops_before_continuation(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'iteration-limit.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-014"))
        gateway = FakeResponsesGateway([
            call_turn("r1", ("c1", "get_disk_usage", {"device_id": "WS-002"}))
        ])
        service = AIInvestigationService(
            InvestigationRepository(session), InvestigationToolRegistry(), gateway,
            max_response_iterations=1, max_tool_calls=5, max_identical_tool_calls=2,
        )
        with pytest.raises(AIInvestigationError) as error:
            service.investigate(incident)
        assert error.value.code == "ai_loop_limit_reached"
        assert gateway.continuations == []
    engine.dispose()


def test_settings_can_exist_without_api_key(monkeypatch) -> None:
    from core.config import Settings

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert Settings().openai_api_key is None


def test_ai_run_starts_running_before_completion(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'running.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(
            select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-019")
        )
        run = InvestigationRepository(session).start_ai_run(
            incident.id, "fake-responses-model"
        )
        assert run.status == AIInvestigationStatus.RUNNING
        assert run.completed_at is None
    engine.dispose()


def test_running_conflict_returns_clean_api_error(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def conflict(*args, **kwargs):
        raise ActiveInvestigationExistsError(19, "ai")

    monkeypatch.setattr(InvestigationRepository, "start_ai_run", conflict)
    response = run_with_fake(
        seeded_client, "INC-019", FakeResponsesGateway([final_turn()])
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "investigation_already_running"
