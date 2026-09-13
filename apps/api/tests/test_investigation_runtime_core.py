import json
from types import SimpleNamespace

import pytest

from domain.ai import InvestigationEventType, InvestigationRuntime
from domain.investigation import InvestigationOrigin, ToolResult
from services.investigation_runtime_core import (
    InvestigationRuntimeCore,
    InvestigationToolLimitError,
)
from services.investigation_input import (
    build_investigation_goal,
    build_investigation_input,
)
from services.investigation_plan import build_model_guided_investigation_plan


class RecordingRegistry:
    transport = "direct"

    def dispatch(self, name: str, raw_arguments: str):
        return json.loads(raw_arguments), ToolResult(
            tool=name,
            resource="WS-002",
            success=True,
            data={"observed": True},
        )


class RecordingRepository:
    def __init__(self):
        self.calls = []

    def record_result(self, incident_id, result, **kwargs):
        self.calls.append((incident_id, result, kwargs))
        return SimpleNamespace(id=100 + len(self.calls))


class RecordingEvents:
    def __init__(self):
        self.items = []

    def record(self, event_type, **fields):
        self.items.append((event_type, fields))


def execution_input(runtime: InvestigationRuntime):
    incident = SimpleNamespace(
        id=14,
        catalog_id="INC-TEST",
        title="Reported symptom",
        description="Test incident",
        category="endpoint",
        priority=SimpleNamespace(value="high"),
        affected_resource_type="device",
        affected_resource_id="WS-002",
        investigation_context={"device_id": "WS-002"},
    )
    goal = build_investigation_goal()
    plan = build_model_guided_investigation_plan(goal)
    return build_investigation_input(incident, goal, plan, runtime.value)


@pytest.mark.parametrize(
    ("runtime", "origin"),
    [
        (InvestigationRuntime.MANUAL_RESPONSES, InvestigationOrigin.AI),
        (InvestigationRuntime.AGENTS_SDK, InvestigationOrigin.AGENTS_SDK),
    ],
)
def test_shared_core_persists_owned_evidence_and_common_events(runtime, origin):
    repository = RecordingRepository()
    events = RecordingEvents()
    governed_input = execution_input(runtime)
    core = InvestigationRuntimeCore(
        repository=repository,
        tools=RecordingRegistry(),
        execution_input=governed_input,
        investigation_id=27,
        origin=origin,
        max_tool_calls=2,
        max_identical_tool_calls=2,
        events=events,
    )

    call = core.execute("get_disk_usage", '{"device_id":"WS-002"}', "call-1", 1)

    assert core.execution_input is governed_input
    assert core.execution_input.plan is governed_input.plan
    assert call.evidence_id == 101
    assert json.loads(call.output)["evidence_id"] == 101
    assert repository.calls[0][2] == {
        "origin": origin,
        "arguments": {"device_id": "WS-002"},
        "investigation_id": 27,
    }
    assert [item[0] for item in events.items] == [
        InvestigationEventType.TOOL_STARTED,
        InvestigationEventType.TOOL_COMPLETED,
    ]


def test_shared_core_owns_repeated_call_limit_before_second_persistence():
    repository = RecordingRepository()
    core = InvestigationRuntimeCore(
        repository=repository,
        tools=RecordingRegistry(),
        execution_input=execution_input(InvestigationRuntime.MANUAL_RESPONSES),
        investigation_id=27,
        origin=InvestigationOrigin.AI,
        max_tool_calls=5,
        max_identical_tool_calls=1,
    )

    core.execute("get_disk_usage", '{"device_id":"WS-002"}', "call-1", 1)
    with pytest.raises(InvestigationToolLimitError) as error:
        core.execute("get_disk_usage", '{"device_id":"WS-002"}', "call-2", 2)

    assert error.value.code == "ai_repeated_call_limit"
    assert len(repository.calls) == 1
