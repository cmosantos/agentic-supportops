from fastapi.testclient import TestClient

from api.dependencies import get_agents_sdk_model
from integrations.responses_gateway import ResponsesProviderError
from main import app
from tests.fakes import FakeResponsesGateway, call_turn, final_turn
from tests.test_agents_sdk_investigation import (
    FakeAgentsModel,
    delegation_response,
    final_response,
    specialist_final_response,
    tool_response,
)


def run_manual(client: TestClient, gateway):
    from api.dependencies import get_responses_gateway

    app.dependency_overrides[get_responses_gateway] = lambda: gateway
    try:
        return client.post("/incidents/INC-019/investigate-ai")
    finally:
        app.dependency_overrides.pop(get_responses_gateway, None)


def run_sdk(client: TestClient, model):
    app.dependency_overrides[get_agents_sdk_model] = lambda: model
    try:
        return client.post("/incidents/INC-019/investigate-agent-sdk")
    finally:
        app.dependency_overrides.pop(get_agents_sdk_model, None)


def events(client: TestClient, runtime: str, incident: str = "INC-019"):
    response = client.get(f"/incidents/{incident}/investigations/{runtime}/events")
    assert response.status_code == 200, response.text
    return response.json()


def test_manual_timeline_orders_turns_tools_tokens_and_completion(
    seeded_client: TestClient,
) -> None:
    gateway = FakeResponsesGateway(
        [
            call_turn(
                "resp-tools",
                (
                    "call-external",
                    "check_external_connectivity",
                    {"device_id": "WS-003"},
                ),
                (
                    "call-dns",
                    "check_dns_resolution",
                    {"device_id": "WS-003", "hostname": "portal.contoso.example"},
                ),
                ("call-config", "get_network_config", {"device_id": "WS-003"}),
            ),
            final_turn(),
        ]
    )
    assert run_manual(seeded_client, gateway).status_code == 200
    timeline = events(seeded_client, "manual_responses")

    assert [item["sequence"] for item in timeline] == list(
        range(1, len(timeline) + 1)
    )
    assert timeline[0]["event_type"] == "run_started"
    assert timeline[-1]["event_type"] == "run_completed"
    assert [
        item["model_turn"]
        for item in timeline
        if item["event_type"] == "model_turn_completed"
    ] == [1, 2]
    requested = [item for item in timeline if item["event_type"] == "tool_requested"]
    assert [item["tool_name"] for item in requested] == [
        "check_external_connectivity",
        "check_dns_resolution",
        "get_network_config",
    ]
    assert all(item["model_turn"] == 1 for item in requested)
    completed_turns = [
        item for item in timeline if item["event_type"] == "model_turn_completed"
    ]
    assert sum(item["total_tokens"] for item in completed_turns) == 24
    assert all(
        item["duration_ms"] >= 0
        for item in timeline
        if item["duration_ms"] is not None
    )
    serialized = str(timeline).lower()
    assert "authorization" not in serialized
    assert "api_key" not in serialized


def test_sdk_timeline_uses_raw_model_responses_and_function_tool_boundaries(
    seeded_client: TestClient,
) -> None:
    assert run_sdk(
        seeded_client,
        FakeAgentsModel(
            [
                delegation_response(),
                tool_response(),
                specialist_final_response(),
                final_response(),
            ]
        ),
    ).status_code == 200
    timeline = events(seeded_client, "agents_sdk")

    assert timeline[0]["event_type"] == "run_started"
    assert timeline[-1]["event_type"] == "run_completed"
    requested = [item for item in timeline if item["event_type"] == "tool_requested"]
    completed = [item for item in timeline if item["event_type"] == "tool_completed"]
    assert len(requested) == len(completed) == 4
    assert requested[0]["tool_name"] == "investigate_endpoint_network"
    assert completed[-1]["metadata"]["kind"] == "agent_delegation"
    turns = [item for item in timeline if item["event_type"] == "model_turn_completed"]
    assert [item["response_id"] for item in turns] == [
        "resp-sdk-delegate",
        "resp-sdk-tools",
        "resp-sdk-specialist-final",
        "resp-sdk-final",
    ]
    assert sum(item["input_tokens"] for item in turns) == 270
    assert sum(item["output_tokens"] for item in turns) == 90
    assert sum(item["total_tokens"] for item in turns) == 360
    final = next(item for item in timeline if item["event_type"] == "final_output")
    assert final["metadata"]["final_agent"] == "SupportOps Orchestrator"


def test_runtime_timelines_coexist_and_are_isolated(seeded_client: TestClient) -> None:
    assert run_manual(seeded_client, FakeResponsesGateway([final_turn()])).status_code == 200
    assert run_sdk(seeded_client, FakeAgentsModel([final_response()])).status_code == 200

    manual = events(seeded_client, "manual_responses")
    sdk = events(seeded_client, "agents_sdk")
    assert {item["runtime"] for item in manual} == {"manual_responses"}
    assert {item["runtime"] for item in sdk} == {"agents_sdk"}
    assert manual[0]["investigation_id"] != sdk[0]["investigation_id"]
    missing = seeded_client.get(
        "/incidents/INC-018/investigations/manual_responses/events"
    )
    assert missing.status_code == 404


def test_old_investigation_without_events_remains_readable(tmp_path) -> None:
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker

    from db.base import Base
    from db.models import IncidentRecord
    from repositories.investigation_repository import InvestigationRepository
    from simulation.seed import seed_catalog

    engine = create_engine(f"sqlite:///{tmp_path / 'old-run.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(
            select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-019")
        )
        repository = InvestigationRepository(session)
        run = repository.start_ai_run(incident.id, "legacy-model")
        assert repository.get_ai_run(incident.id).id == run.id
        assert repository.list_events(run.id) == []
    engine.dispose()


def test_manual_failure_persists_failed_timeline(seeded_client: TestClient) -> None:
    class FailedGateway:
        model = "fake-responses-model"

        def create_initial(self, incident_input: str):
            raise ResponsesProviderError("ai_provider_unavailable", "offline")

    assert run_manual(seeded_client, FailedGateway()).status_code == 502
    timeline = events(seeded_client, "manual_responses")
    assert [item["event_type"] for item in timeline] == [
        "run_started",
        "model_turn_started",
        "model_turn_completed",
        "run_failed",
    ]
    assert timeline[-1]["metadata"] == {"error_code": "ai_provider_unavailable"}


def test_sdk_failure_and_tool_application_failure_are_observable(
    seeded_client: TestClient,
) -> None:
    assert run_sdk(
        seeded_client, FakeAgentsModel(error=RuntimeError("provider internals"))
    ).status_code == 502
    failed = events(seeded_client, "agents_sdk")
    assert failed[-1]["event_type"] == "run_failed"
    assert failed[-1]["metadata"] == {"error_code": "agents_sdk_failure"}
    assert "provider internals" not in str(failed)

    gateway = FakeResponsesGateway(
        [
            call_turn(
                "resp-failed-tool",
                ("call-missing", "get_network_config", {"device_id": "WS-999"}),
            ),
            final_turn(),
        ]
    )
    assert run_manual(seeded_client, gateway).status_code == 200
    manual = events(seeded_client, "manual_responses")
    tool_failed = next(item for item in manual if item["event_type"] == "tool_failed")
    assert tool_failed["status"] == "failed"
    assert tool_failed["arguments"] == {"device_id": "WS-999"}


def test_reexecution_preserves_history_and_latest_timeline(
    seeded_client: TestClient,
) -> None:
    assert run_manual(
        seeded_client,
        FakeResponsesGateway(
            [
                call_turn(
                    "first-tools",
                    ("first-call", "get_network_config", {"device_id": "WS-003"}),
                ),
                final_turn(),
            ]
        ),
    ).status_code == 200
    first = events(seeded_client, "manual_responses")
    first_run_id = first[0]["investigation_id"]
    assert any(item["tool_call_id"] == "first-call" for item in first)

    assert run_manual(seeded_client, FakeResponsesGateway([final_turn()])).status_code == 200
    second = events(seeded_client, "manual_responses")
    second_run_id = second[0]["investigation_id"]
    assert second[0]["sequence"] == 1
    assert second_run_id != first_run_id
    assert not any(item["tool_call_id"] == "first-call" for item in second)
    history = seeded_client.get(
        "/incidents/INC-019/investigation-runs?runtime=manual_responses"
    ).json()
    assert [item["id"] for item in history] == [second_run_id, first_run_id]
    historical_events = seeded_client.get(
        f"/incidents/INC-019/investigation-runs/{first_run_id}/events"
    )
    assert historical_events.status_code == 200
    assert any(
        item["tool_call_id"] == "first-call" for item in historical_events.json()
    )


def test_event_schema_is_idempotent_and_sqlite_remains_healthy(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect, select, text
    from sqlalchemy.orm import sessionmaker

    from db.base import Base
    from db.models import IncidentRecord
    from domain.ai import InvestigationEventType, InvestigationRuntime
    from repositories.investigation_repository import InvestigationRepository
    from simulation.seed import seed_catalog

    engine = create_engine(f"sqlite:///{tmp_path / 'event-integrity.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
        incident = session.scalar(
            select(IncidentRecord).where(IncidentRecord.catalog_id == "INC-019")
        )
        repository = InvestigationRepository(session)
        run = repository.start_ai_run(incident.id, "fake-model")
        repository.record_event(
            run.id,
            InvestigationRuntime.MANUAL_RESPONSES,
            InvestigationEventType.RUN_STARTED,
            1,
            status="running",
        )

    Base.metadata.create_all(engine)
    assert "investigation_events" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA quick_check")).scalar_one() == "ok"
        assert connection.execute(
            text("SELECT COUNT(*) FROM investigation_events")
        ).scalar_one() == 1
    engine.dispose()
