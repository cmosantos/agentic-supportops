from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionRecord,
    ActionProposalRecord,
    AIInvestigationRecord,
    OutcomeVerificationRecord,
)
from domain.ai import InvestigationEventType, InvestigationRuntime
from domain.outcome_verification import OutcomeVerificationStatus
from repositories.investigation_repository import InvestigationRepository


class OutcomeVerificationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = InvestigationRepository(session)

    def get_execution_and_proposal(
        self, execution_id: int
    ) -> tuple[ActionExecutionRecord, ActionProposalRecord] | None:
        row = self._session.execute(
            select(ActionExecutionRecord, ActionProposalRecord)
            .join(
                ActionProposalRecord,
                ActionProposalRecord.id == ActionExecutionRecord.proposal_id,
            )
            .where(ActionExecutionRecord.id == execution_id)
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None

    def get_for_execution(self, execution_id: int) -> OutcomeVerificationRecord | None:
        return self._session.scalar(
            select(OutcomeVerificationRecord).where(
                OutcomeVerificationRecord.execution_id == execution_id
            )
        )

    def runtime_for(self, proposal: ActionProposalRecord) -> InvestigationRuntime:
        investigation = self._session.get(AIInvestigationRecord, proposal.investigation_id)
        return (
            InvestigationRuntime.AGENTS_SDK
            if investigation is not None and investigation.mode == "agents_sdk"
            else InvestigationRuntime.MANUAL_RESPONSES
        )

    def start(
        self,
        execution: ActionExecutionRecord,
        proposal: ActionProposalRecord,
        runtime: InvestigationRuntime,
        expected_outcome: dict,
    ) -> tuple[OutcomeVerificationRecord, bool]:
        existing = self.get_for_execution(execution.id)
        if existing is not None:
            return existing, False
        record = OutcomeVerificationRecord(
            execution_id=execution.id,
            proposal_id=proposal.id,
            incident_id=proposal.incident_id,
            status=OutcomeVerificationStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            expected_outcome=expected_outcome,
        )
        self._session.add(record)
        try:
            self._session.flush()
            sequence = self._events.next_event_sequence(proposal.investigation_id)
            metadata = self._metadata(proposal, execution, record)
            for offset, event_type in enumerate(
                (
                    InvestigationEventType.VERIFICATION_REQUESTED,
                    InvestigationEventType.VERIFICATION_STARTED,
                )
            ):
                self._events.record_event(
                    proposal.investigation_id,
                    runtime,
                    event_type,
                    sequence + offset,
                    commit=False,
                    status=record.status.value,
                    metadata=metadata,
                )
            self._commit()
        except IntegrityError:
            self._session.rollback()
            existing = self.get_for_execution(execution.id)
            if existing is None:
                raise
            return existing, False
        self._session.refresh(record)
        return record, True

    def finish(
        self,
        execution: ActionExecutionRecord,
        proposal: ActionProposalRecord,
        record: OutcomeVerificationRecord,
        runtime: InvestigationRuntime,
        status: OutcomeVerificationStatus,
        observed_outcome: dict | None,
        evidence: dict | None,
        error: dict | None,
    ) -> OutcomeVerificationRecord:
        record.status = status
        record.completed_at = datetime.now(timezone.utc)
        record.observed_outcome = observed_outcome
        record.evidence = evidence
        record.error = error
        event_type = {
            OutcomeVerificationStatus.VERIFIED: InvestigationEventType.VERIFICATION_VERIFIED,
            OutcomeVerificationStatus.NOT_VERIFIED: InvestigationEventType.VERIFICATION_NOT_VERIFIED,
            OutcomeVerificationStatus.FAILED: InvestigationEventType.VERIFICATION_FAILED,
        }[status]
        metadata = self._metadata(proposal, execution, record)
        metadata["observed_state"] = (
            str(observed_outcome.get("state")) if observed_outcome else "unavailable"
        )
        self._events.record_event(
            proposal.investigation_id,
            runtime,
            event_type,
            self._events.next_event_sequence(proposal.investigation_id),
            commit=False,
            status=status.value,
            metadata=metadata,
        )
        self._commit()
        self._session.refresh(record)
        return record

    @staticmethod
    def _metadata(proposal, execution, verification) -> dict[str, int | str]:
        return {
            "incident_id": proposal.incident_id,
            "proposal_id": proposal.id,
            "execution_id": execution.id,
            "verification_id": verification.id,
            "target": proposal.target,
        }

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
