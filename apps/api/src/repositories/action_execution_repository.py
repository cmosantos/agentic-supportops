from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import ActionExecutionRecord, ActionProposalRecord
from domain.action_execution import ActionExecutionStatus
from domain.ai import InvestigationEventType, InvestigationRuntime
from repositories.investigation_repository import InvestigationRepository


class ActionExecutionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._investigations = InvestigationRepository(session)

    def get_proposal(
        self, incident_id: int, investigation_id: int, proposal_id: int
    ) -> ActionProposalRecord | None:
        return self._session.scalar(
            select(ActionProposalRecord).where(
                ActionProposalRecord.id == proposal_id,
                ActionProposalRecord.incident_id == incident_id,
                ActionProposalRecord.investigation_id == investigation_id,
            )
        )

    def get_for_proposal(self, proposal_id: int) -> ActionExecutionRecord | None:
        return self._session.scalar(
            select(ActionExecutionRecord).where(
                ActionExecutionRecord.proposal_id == proposal_id
            )
        )

    def start(
        self, proposal: ActionProposalRecord, runtime: InvestigationRuntime
    ) -> tuple[ActionExecutionRecord, bool]:
        existing = self.get_for_proposal(proposal.id)
        if existing is not None:
            return existing, False

        now = datetime.now(timezone.utc)
        record = ActionExecutionRecord(
            proposal_id=proposal.id,
            incident_id=proposal.incident_id,
            capability_name=proposal.action_type.value,
            status=ActionExecutionStatus.RUNNING,
            started_at=now,
        )
        self._session.add(record)
        try:
            self._session.flush()
            sequence = self._investigations.next_event_sequence(
                proposal.investigation_id
            )
            metadata = self._metadata(proposal, record)
            self._investigations.record_event(
                proposal.investigation_id,
                runtime,
                InvestigationEventType.EXECUTION_REQUESTED,
                sequence,
                commit=False,
                status=ActionExecutionStatus.RUNNING.value,
                metadata=metadata,
            )
            self._investigations.record_event(
                proposal.investigation_id,
                runtime,
                InvestigationEventType.EXECUTION_STARTED,
                sequence + 1,
                commit=False,
                status=ActionExecutionStatus.RUNNING.value,
                metadata=metadata,
            )
            self._commit()
        except IntegrityError:
            self._session.rollback()
            existing = self.get_for_proposal(proposal.id)
            if existing is None:
                raise
            return existing, False
        self._session.refresh(record)
        return record, True

    def complete(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        runtime: InvestigationRuntime,
        result: dict,
    ) -> ActionExecutionRecord:
        record.status = ActionExecutionStatus.COMPLETED
        record.completed_at = datetime.now(timezone.utc)
        record.result = result
        self._record_terminal(
            proposal, record, runtime, InvestigationEventType.EXECUTION_COMPLETED
        )
        return record

    def fail(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        runtime: InvestigationRuntime,
        error: dict,
    ) -> ActionExecutionRecord:
        record.status = ActionExecutionStatus.FAILED
        record.completed_at = datetime.now(timezone.utc)
        record.error = error
        self._record_terminal(
            proposal, record, runtime, InvestigationEventType.EXECUTION_FAILED
        )
        return record

    def _record_terminal(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        runtime: InvestigationRuntime,
        event_type: InvestigationEventType,
    ) -> None:
        self._investigations.record_event(
            proposal.investigation_id,
            runtime,
            event_type,
            self._investigations.next_event_sequence(proposal.investigation_id),
            commit=False,
            status=record.status.value,
            metadata=self._metadata(proposal, record),
        )
        self._commit()
        self._session.refresh(record)

    @staticmethod
    def _metadata(
        proposal: ActionProposalRecord, record: ActionExecutionRecord
    ) -> dict[str, int | str]:
        return {
            "incident_id": proposal.incident_id,
            "proposal_id": proposal.id,
            "execution_id": record.id,
            "capability_name": record.capability_name,
        }

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
