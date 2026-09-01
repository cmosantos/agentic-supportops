from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionRecord,
    ActionProposalRecord,
    InvestigationEventRecord,
)


@dataclass(frozen=True)
class ActionExecutionTimelineContext:
    execution: ActionExecutionRecord
    events: list[InvestigationEventRecord]


class ActionExecutionTimelineRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_context(
        self, execution_id: int
    ) -> ActionExecutionTimelineContext | None:
        row = self._session.execute(
            select(ActionExecutionRecord, ActionProposalRecord)
            .join(
                ActionProposalRecord,
                ActionProposalRecord.id == ActionExecutionRecord.proposal_id,
            )
            .where(ActionExecutionRecord.id == execution_id)
        ).one_or_none()
        if row is None:
            return None

        execution, proposal = row
        investigation_events = self._session.scalars(
            select(InvestigationEventRecord).where(
                InvestigationEventRecord.investigation_id == proposal.investigation_id
            )
        )
        events = [
            event
            for event in investigation_events
            if (event.event_metadata or {}).get("execution_id") == execution.id
        ]
        events.sort(key=lambda event: (event.timestamp, event.sequence, event.id))
        return ActionExecutionTimelineContext(execution, events)
