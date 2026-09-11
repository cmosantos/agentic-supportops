import json
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from db.models import IncidentRecord
from domain.incident import IncidentPriority
from domain.investigation import InvestigationGoal
from services import ai_investigation_service, agents_sdk_investigation_service
from services.investigation_input import (
    build_investigation_goal,
    build_investigation_input,
)
from tests.fakes import FakeResponsesGateway, final_turn
from tests.test_ai_investigation import run_with_fake as run_manual
from tests.test_agents_sdk_investigation import (
    FakeAgentsModel, final_response, run_with_fake as run_sdk,
)


@pytest.fixture
def incident():
    return IncidentRecord(
        catalog_id="INC-goal", title="DNS failure alleged", description="Reported symptom",
        category="network", priority=next(iter(IncidentPriority)),
        affected_resource_type="device", affected_resource_id=" Ws-003/Exact ",
        investigation_context={"host_id": "Host-A", "hostname": "Portal.Example"},
    )


def test_goal_contract_forbids_unexpected_fields(incident):
    data = json.loads(build_investigation_input(incident))["goal"]
    assert InvestigationGoal.model_validate(data).human_action_required is True
    assert InvestigationGoal.model_json_schema()["additionalProperties"] is False
    with pytest.raises(ValidationError) as error:
        InvestigationGoal.model_validate({**data, "plan": ["invented step"]})
    assert error.value.errors()[0]["type"] == "extra_forbidden"


def test_input_preserves_incident_separately_and_deterministically(incident):
    raw = build_investigation_input(incident)
    assert build_investigation_input(incident) == raw
    payload = json.loads(raw)
    assert set(payload) == {"goal", "incident"}
    assert payload["incident"] == {
        "catalog_id": incident.catalog_id, "title": incident.title,
        "description": incident.description, "category": incident.category,
        "priority": incident.priority.value,
        "affected_resource_type": "device", "affected_resource_id": " Ws-003/Exact ",
        "investigation_context": {"host_id": "Host-A", "hostname": "Portal.Example"},
    }
    incident.affected_resource_type = None
    incident.affected_resource_id = None
    incident.catalog_id = None
    incident.investigation_context = {}
    sparse = json.loads(build_investigation_input(incident))
    assert sparse["incident"]["affected_resource_id"] is None
    assert sparse["incident"]["affected_resource_type"] is None
    assert sparse["incident"]["catalog_id"] is None
    assert sparse["incident"]["investigation_context"] == {}
    assert sparse["goal"] == payload["goal"]


def test_goal_describes_evidence_and_safety_boundaries(incident):
    goal = InvestigationGoal.model_validate(json.loads(build_investigation_input(incident))["goal"])
    criteria = " ".join(goal.success_criteria).lower()
    constraints = " ".join(goal.constraints).lower()
    assert "persisted tool evidence" in criteria
    assert "insufficient or conflicting" in criteria
    assert "identifiers exactly" in criteria
    assert "observed facts" in criteria and "assessment" in criteria
    assert "do not treat any remediation as executed" in criteria
    assert "title is a symptom label" in constraints and "not proof" in constraints
    assert "read-only" in constraints and "mutations are outside" in constraints
    assert "never invent" in constraints and "resource scope" in constraints
    assert all(term in constraints for term in ["tool-call", "repeated-call", "turn limits"])
    assert "human review and approval" in constraints
    assert "one bounded remediation" in goal.objective
    assert goal.human_action_required is True


def test_both_runtimes_call_shared_builder_and_deliver_same_payload(seeded_client, monkeypatch):
    assert ai_investigation_service.build_investigation_goal is build_investigation_goal
    assert agents_sdk_investigation_service.build_investigation_goal is build_investigation_goal
    assert ai_investigation_service.build_investigation_input is build_investigation_input
    assert agents_sdk_investigation_service.build_investigation_input is build_investigation_input
    manual_goals = []
    sdk_goals = []

    def build_manual_goal():
        goal = build_investigation_goal()
        manual_goals.append(goal)
        return goal

    def build_sdk_goal():
        goal = build_investigation_goal()
        sdk_goals.append(goal)
        return goal

    manual_goal_builder = Mock(side_effect=build_manual_goal)
    sdk_goal_builder = Mock(side_effect=build_sdk_goal)
    manual_builder = Mock(wraps=build_investigation_input)
    sdk_builder = Mock(wraps=build_investigation_input)
    monkeypatch.setattr(ai_investigation_service, "build_investigation_goal", manual_goal_builder)
    monkeypatch.setattr(agents_sdk_investigation_service, "build_investigation_goal", sdk_goal_builder)
    monkeypatch.setattr(ai_investigation_service, "build_investigation_input", manual_builder)
    monkeypatch.setattr(agents_sdk_investigation_service, "build_investigation_input", sdk_builder)
    gateway = FakeResponsesGateway([final_turn()])
    model = FakeAgentsModel([final_response()])
    manual = run_manual(seeded_client, "INC-019", gateway)
    sdk = run_sdk(seeded_client, model)
    assert manual.status_code == 200, manual.text
    assert sdk.status_code == 200, sdk.text
    manual_goal_builder.assert_called_once_with()
    sdk_goal_builder.assert_called_once_with()
    manual_builder.assert_called_once()
    sdk_builder.assert_called_once()
    assert manual_builder.call_args.args[1] is manual_goals[0]
    assert sdk_builder.call_args.args[1] is sdk_goals[0]
    expected = build_investigation_input(
        manual_builder.call_args.args[0], manual_goals[0]
    )
    assert expected == build_investigation_input(
        sdk_builder.call_args.args[0], sdk_goals[0]
    )
    assert gateway.initial_inputs == [expected]
    sdk_input = model.calls[0]["input"]
    if isinstance(sdk_input, list):
        sdk_input = next(item["content"] for item in sdk_input if item.get("role") == "user")
    assert sdk_input == expected
    for response in [manual, sdk]:
        investigation = response.json()["investigation"]
        assert InvestigationGoal.model_validate(investigation["goal_snapshot"])
        assert investigation["goal_snapshot"] == json.loads(expected)["goal"]
        result = investigation["result"]
        assert result["human_action_required"] is True
        assert result["status"] == "insufficient_evidence"
        assert result["evidence_ids"] == []


def test_failed_responses_run_keeps_goal_snapshot(seeded_client):
    class FailedGateway:
        model = "fake-responses-model"

        def __init__(self):
            self.initial_inputs = []

        def create_initial(self, incident_input: str):
            self.initial_inputs.append(incident_input)
            from integrations.responses_gateway import ResponsesProviderError

            raise ResponsesProviderError("ai_rate_limited", "OpenAI rate limit reached")

    gateway = FailedGateway()
    response = run_manual(seeded_client, "INC-019", gateway)
    assert response.status_code == 502

    stored = seeded_client.get("/incidents/INC-019/ai-investigation").json()[
        "investigation"
    ]
    assert stored["status"] == "failed"
    assert stored["goal_snapshot"] == json.loads(gateway.initial_inputs[0])["goal"]
    assert InvestigationGoal.model_validate(stored["goal_snapshot"])


def test_historical_goal_snapshot_is_not_reconstructed_or_changed(
    seeded_client, monkeypatch
):
    original = build_investigation_goal()
    goal_a = original.model_copy(update={"objective": "Historical objective A"})
    goal_b = original.model_copy(update={"objective": "Later objective B"})
    goal_builder = Mock(side_effect=[goal_a, goal_b])
    monkeypatch.setattr(ai_investigation_service, "build_investigation_goal", goal_builder)

    first = run_manual(
        seeded_client, "INC-019", FakeResponsesGateway([final_turn("goal-a")])
    ).json()
    second = run_manual(
        seeded_client, "INC-019", FakeResponsesGateway([final_turn("goal-b")])
    ).json()

    first_id = first["investigation"]["id"]
    historical = seeded_client.get(
        f"/incidents/INC-019/investigation-runs/{first_id}"
    ).json()
    assert historical["goal_snapshot"] == goal_a.model_dump(mode="json")
    assert second["investigation"]["goal_snapshot"] == goal_b.model_dump(mode="json")
