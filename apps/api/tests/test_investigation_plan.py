import json

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.dependencies import get_controlled_tools
from db.base import Base
from db.models import AIInvestigationRecord
from domain.ai import AIInvestigationRead
from domain.investigation import InvestigationPlan, InvestigationPlanStep
from integrations.responses_gateway import ResponsesProviderError
from main import app
from repositories.investigation_repository import InvestigationRepository
from services import ai_investigation_service, agents_sdk_investigation_service
from services.investigation_input import build_investigation_goal
from services.investigation_plan import (
    build_deterministic_investigation_plan,
    build_model_guided_investigation_plan,
)
from services.playbooks import PlaybookStep
from services.tool_registry import InvestigationToolRegistry
from tests.fakes import FakeResponsesGateway, final_turn
from tests.test_ai_investigation import run_with_fake as run_manual
from tests.test_agents_sdk_investigation import (
    FakeAgentsModel,
    final_response,
    run_with_fake as run_sdk,
)


def test_plan_contract_is_strict_and_contains_intent_only() -> None:
    step = InvestigationPlanStep(
        sequence=1,
        intended_action="Inspect the affected account.",
        purpose="Establish factual account state for the investigation goal.",
    )
    plan = InvestigationPlan(steps=[step])

    assert plan.model_dump() == {
        "steps": [
            {
                "sequence": 1,
                "intended_action": "Inspect the affected account.",
                "purpose": "Establish factual account state for the investigation goal.",
            }
        ]
    }
    assert InvestigationPlan.model_json_schema()["additionalProperties"] is False

    for forbidden in ("status", "result", "evidence_ids", "agent_name", "reasoning"):
        with pytest.raises(ValidationError) as error:
            InvestigationPlanStep.model_validate(
                {**step.model_dump(), forbidden: "not allowed"}
            )
        assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    "sequences",
    [[], [2], [1, 1], [1, 3], [2, 1]],
)
def test_plan_requires_non_empty_contiguous_ordered_sequence(
    sequences: list[int],
) -> None:
    with pytest.raises(ValidationError):
        InvestigationPlan(
            steps=[
                InvestigationPlanStep(
                    sequence=sequence,
                    intended_action=f"Action {sequence}",
                    purpose=f"Purpose {sequence}",
                )
                for sequence in sequences
            ]
        )


def test_model_guided_plan_is_stable_and_derived_from_exact_goal() -> None:
    original = build_investigation_goal()
    changed = original.model_copy(
        update={"objective": "Determine whether the affected application is available."}
    )

    first = build_model_guided_investigation_plan(original)
    second = build_model_guided_investigation_plan(original)
    changed_plan = build_model_guided_investigation_plan(changed)

    assert first == second
    assert [step.sequence for step in first.steps] == [1, 2, 3]
    assert original.objective in first.steps[0].purpose
    assert changed.objective in changed_plan.steps[0].purpose
    assert first != changed_plan


def test_deterministic_plan_truthfully_projects_resolved_playbook() -> None:
    goal = build_investigation_goal()
    resolved_playbook = [
        (
            PlaybookStep("get_account_status", {"user_id": "$user_id"}),
            {"user_id": "USR-001"},
        ),
        (
            PlaybookStep("get_mailbox", {"reference": "$mailbox_id"}),
            {"reference": "MBX-001"},
        ),
    ]
    registry = InvestigationToolRegistry()

    plan = build_deterministic_investigation_plan(
        goal, resolved_playbook, registry
    )

    assert [step.sequence for step in plan.steps] == [1, 2]
    assert "get_account_status" in plan.steps[0].intended_action
    assert "USR-001" in plan.steps[0].intended_action
    assert registry.description("get_account_status") in plan.steps[0].purpose
    assert "get_mailbox" in plan.steps[1].intended_action
    assert "MBX-001" in plan.steps[1].intended_action
    assert goal.objective in plan.steps[1].purpose


def test_repository_persists_typed_plan_snapshot_with_the_goal(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'plan-snapshot.db'}")
    Base.metadata.create_all(engine)
    goal = build_investigation_goal()
    plan = build_model_guided_investigation_plan(goal)

    with Session(engine) as session:
        run = InvestigationRepository(session).start_ai_run(
            1,
            "test-model",
            goal_snapshot=goal,
            plan_snapshot=plan,
        )
        read = AIInvestigationRead.model_validate(run)

        assert run.plan_snapshot == plan.model_dump(mode="json")
        assert read.plan_snapshot == plan

    with Session(engine) as session:
        stored = session.get(AIInvestigationRecord, run.id)
        assert stored is not None
        assert stored.plan_snapshot == plan.model_dump(mode="json")
    engine.dispose()


def test_deterministic_runtime_persists_truthful_playbook_projection(
    seeded_client,
) -> None:
    response = seeded_client.post("/incidents/INC-019/investigate")

    assert response.status_code == 200, response.text
    body = response.json()
    plan = InvestigationPlan.model_validate(
        body["investigation"]["plan_snapshot"]
    )
    actual_tools = [step["tool"] for step in body["steps"]]

    assert [step.sequence for step in plan.steps] == [1, 2, 3, 4]
    assert all(
        tool in planned.intended_action
        for tool, planned in zip(actual_tools, plan.steps, strict=True)
    )
    assert all("status" not in step for step in body["investigation"]["plan_snapshot"]["steps"])


def test_model_guided_runtimes_receive_the_exact_persisted_plan(
    seeded_client, monkeypatch
) -> None:
    expected = build_model_guided_investigation_plan(build_investigation_goal())
    monkeypatch.setattr(
        ai_investigation_service,
        "build_model_guided_investigation_plan",
        lambda _goal: expected,
    )
    manual_input = []
    original_manual_input = ai_investigation_service.build_investigation_input

    def capture_manual_input(incident, goal, plan):
        manual_input.append(plan)
        return original_manual_input(incident, goal, plan)

    monkeypatch.setattr(
        ai_investigation_service, "build_investigation_input", capture_manual_input
    )
    gateway = FakeResponsesGateway([final_turn("plan-manual")])
    manual = run_manual(seeded_client, "INC-019", gateway)

    sdk_input = []
    monkeypatch.setattr(
        agents_sdk_investigation_service,
        "build_model_guided_investigation_plan",
        lambda _goal: expected,
    )
    original_sdk_input = agents_sdk_investigation_service.build_investigation_input

    def capture_sdk_input(incident, goal, plan):
        sdk_input.append(plan)
        return original_sdk_input(incident, goal, plan)

    monkeypatch.setattr(
        agents_sdk_investigation_service, "build_investigation_input", capture_sdk_input
    )
    model = FakeAgentsModel([final_response()])
    sdk = run_sdk(seeded_client, model)

    assert manual.status_code == 200, manual.text
    assert sdk.status_code == 200, sdk.text
    assert manual_input == [expected]
    assert manual_input[0] is expected
    assert sdk_input == [expected]
    assert sdk_input[0] is expected
    expected_payload = expected.model_dump(mode="json")
    assert json.loads(gateway.initial_inputs[0])["plan"] == expected_payload
    sdk_provider_input = model.calls[0]["input"]
    if isinstance(sdk_provider_input, list):
        sdk_provider_input = next(
            item["content"]
            for item in sdk_provider_input
            if item.get("role") == "user"
        )
    assert json.loads(sdk_provider_input)["plan"] == expected_payload
    assert manual.json()["investigation"]["plan_snapshot"] == expected_payload
    assert sdk.json()["investigation"]["plan_snapshot"] == expected_payload


def test_failed_responses_run_keeps_plan_committed_before_provider_call(
    seeded_client,
) -> None:
    class FailedGateway:
        model = "failed-plan-model"

        def __init__(self):
            self.initial_inputs = []

        def create_initial(self, incident_input: str):
            self.initial_inputs.append(incident_input)
            raise ResponsesProviderError(
                "ai_rate_limited", "OpenAI rate limit reached"
            )

    gateway = FailedGateway()
    response = run_manual(seeded_client, "INC-019", gateway)

    assert response.status_code == 502
    stored = seeded_client.get("/incidents/INC-019/ai-investigation").json()[
        "investigation"
    ]
    assert stored["status"] == "failed"
    assert stored["plan_snapshot"] == json.loads(gateway.initial_inputs[0])["plan"]
    assert InvestigationPlan.model_validate(stored["plan_snapshot"])


def test_failed_deterministic_run_keeps_plan_committed_before_tool_call(
    seeded_client, monkeypatch
) -> None:
    tools = InvestigationToolRegistry()
    monkeypatch.setattr(
        tools,
        "execute",
        lambda _name, _arguments: (_ for _ in ()).throw(
            RuntimeError("simulated tool failure")
        ),
    )
    app.dependency_overrides[get_controlled_tools] = lambda: tools

    with pytest.raises(RuntimeError, match="simulated tool failure"):
        seeded_client.post("/incidents/INC-019/investigate")

    history = seeded_client.get("/incidents/INC-019/investigation-runs").json()
    assert history[0]["status"] == "failed"
    assert InvestigationPlan.model_validate(history[0]["plan_snapshot"])


def test_historical_plan_snapshot_is_not_reconstructed_or_changed(
    seeded_client, monkeypatch
) -> None:
    base = build_model_guided_investigation_plan(build_investigation_goal())
    plan_a = base.model_copy(
        update={
            "steps": [
                base.steps[0].model_copy(
                    update={"intended_action": "Historical intended action A."}
                ),
                *base.steps[1:],
            ]
        }
    )
    plan_b = base.model_copy(
        update={
            "steps": [
                base.steps[0].model_copy(
                    update={"intended_action": "Later intended action B."}
                ),
                *base.steps[1:],
            ]
        }
    )
    plans = iter([plan_a, plan_b])
    monkeypatch.setattr(
        ai_investigation_service,
        "build_model_guided_investigation_plan",
        lambda _goal: next(plans),
    )

    first = run_manual(
        seeded_client, "INC-019", FakeResponsesGateway([final_turn("plan-a")])
    ).json()
    second = run_manual(
        seeded_client, "INC-019", FakeResponsesGateway([final_turn("plan-b")])
    ).json()
    historical = seeded_client.get(
        f"/incidents/INC-019/investigation-runs/{first['investigation']['id']}"
    ).json()

    assert historical["plan_snapshot"] == plan_a.model_dump(mode="json")
    assert second["investigation"]["plan_snapshot"] == plan_b.model_dump(mode="json")
