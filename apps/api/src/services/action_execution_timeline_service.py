from domain.action_execution_timeline import ActionExecutionTimelineEntry
from domain.ai import InvestigationEventType
from repositories.action_execution_timeline_repository import (
    ActionExecutionTimelineRepository,
)


class ActionExecutionTimelineNotFoundError(LookupError):
    pass


_DESCRIPTIONS = {
    InvestigationEventType.EXECUTION_REQUESTED: "Controlled execution was requested.",
    InvestigationEventType.EXECUTION_STARTED: "Controlled execution attempt started.",
    InvestigationEventType.EXECUTION_COMPLETED: "Execution reached completed state.",
    InvestigationEventType.EXECUTION_FAILED: "Execution reached failed state.",
    InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN: (
        "Execution attempt ended with an unknown mutation outcome."
    ),
    InvestigationEventType.EXECUTION_ATTEMPT_INTERRUPTION_ASSESSED: (
        "A stale execution attempt was assessed as interrupted."
    ),
    InvestigationEventType.RECONCILIATION_REQUESTED: "Reconciliation was requested.",
    InvestigationEventType.RECONCILIATION_STARTED: (
        "Read-only reconciliation observation started."
    ),
    InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED: (
        "Reconciliation observed the desired state."
    ),
    InvestigationEventType.RECONCILIATION_UNDESIRED_STATE_OBSERVED: (
        "Reconciliation observed an undesired state."
    ),
    InvestigationEventType.RECONCILIATION_INCONCLUSIVE: (
        "Reconciliation could not reach a reliable conclusion."
    ),
    InvestigationEventType.RECONCILIATION_RECOVERY_REQUESTED: (
        "Explicit reconciliation recovery was requested."
    ),
    InvestigationEventType.RECONCILIATION_RECOVERY_STARTED: (
        "Recovered read-only reconciliation observation started."
    ),
    InvestigationEventType.VERIFICATION_REQUESTED: "Outcome verification was requested.",
    InvestigationEventType.VERIFICATION_STARTED: "Independent outcome verification started.",
    InvestigationEventType.VERIFICATION_VERIFIED: (
        "Independent verification observed the expected outcome."
    ),
    InvestigationEventType.VERIFICATION_NOT_VERIFIED: (
        "Independent verification did not observe the expected outcome."
    ),
    InvestigationEventType.VERIFICATION_FAILED: (
        "Independent verification could not collect reliable evidence."
    ),
    InvestigationEventType.RESOLUTION_REVIEWED: (
        "A human reviewed the related verification evidence."
    ),
    InvestigationEventType.INCIDENT_RESOLVED: "A human decision resolved the related incident.",
    InvestigationEventType.INCIDENT_KEPT_OPEN: "A human decision kept the related incident open.",
}


class ActionExecutionTimelineService:
    def __init__(self, repository: ActionExecutionTimelineRepository) -> None:
        self._repository = repository

    def get(self, execution_id: int) -> list[ActionExecutionTimelineEntry]:
        context = self._repository.get_context(execution_id)
        if context is None:
            raise ActionExecutionTimelineNotFoundError(
                f"Action execution {execution_id} not found"
            )
        def metadata_for(event) -> dict:
            return event.event_metadata or {}

        return [
            ActionExecutionTimelineEntry(
                timestamp=event.timestamp,
                event_type=event.event_type,
                execution_id=context.execution.id,
                attempt_id=self._positive_int(metadata_for(event).get("attempt_id")),
                status=event.status,
                description=_DESCRIPTIONS[event.event_type],
                reason=self._reason(metadata_for(event)),
            )
            for event in context.events
            if event.event_type in _DESCRIPTIONS
        ]

    @staticmethod
    def _positive_int(value: object) -> int | None:
        return value if isinstance(value, int) and value > 0 else None

    @staticmethod
    def _reason(metadata: dict) -> str | None:
        for key in (
            "assessment_reason",
            "failure_cause",
            "decision",
            "completion_basis",
            "observed_state",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value
        return None
