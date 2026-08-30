from datetime import datetime, timezone

from domain.action_execution import ActionExecutionStaleAssessmentRead
from domain.ai import InvestigationEventType


def test_stale_assessment_response_contract_contains_execution_and_attempt() -> None:
    now = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)

    response = ActionExecutionStaleAssessmentRead.model_validate(
        {
            "execution": {
                "id": 7,
                "proposal_id": 5,
                "incident_id": 23,
                "capability_name": "restart_simulated_service",
                "status": "running",
                "requested_at": now,
                "started_at": now,
                "completed_at": None,
                "result": None,
                "error": None,
                "completion_basis": None,
            },
            "attempt": {
                "id": 11,
                "execution_id": 7,
                "attempt_number": 1,
                "status": "running",
                "claimed_at": now,
                "invocation_started_at": None,
                "completed_at": None,
                "result": None,
                "error": None,
                "failure_cause": None,
                "outcome_certainty": None,
                "created_at": now,
            },
        }
    )

    assert response.execution.id == 7
    assert response.attempt.execution_id == response.execution.id
    assert response.attempt.attempt_number == 1


def test_interruption_assessment_event_contract_has_stable_value() -> None:
    assert (
        InvestigationEventType.EXECUTION_ATTEMPT_INTERRUPTION_ASSESSED.value
        == "execution_attempt_interruption_assessed"
    )
