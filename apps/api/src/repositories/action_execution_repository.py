from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionRecord,
    ActionProposalRecord,
)
from domain.action_execution import (
    ActionExecutionCompletionBasis,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.ai import InvestigationEventType, InvestigationRuntime
from repositories.action_execution_attempt_repository import (
    ActionExecutionAttemptRepository,
    InvalidActionExecutionAttemptTransitionError,
)
from repositories.investigation_repository import InvestigationRepository


class ActionExecutionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._investigations = InvestigationRepository(session)
        self._attempts = ActionExecutionAttemptRepository(session)

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
    ) -> tuple[
        ActionExecutionRecord, ActionExecutionAttemptRecord | None, bool
    ]:
        existing = self.get_for_proposal(proposal.id)
        if existing is not None:
            return existing, self._attempts.get_canonical_attempt(existing.id), False

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
            attempt = self._attempts.create_first_attempt(record.id, now)
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
            return existing, self._attempts.get_canonical_attempt(existing.id), False
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(record)
        self._session.refresh(attempt)
        return record, attempt, True

    def mark_invocation_started(
        self, attempt: ActionExecutionAttemptRecord
    ) -> ActionExecutionAttemptRecord:
        try:
            self._attempts.mark_invocation_started(
                attempt, datetime.now(timezone.utc)
            )
            self._commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(attempt)
        return attempt

    def complete(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        runtime: InvestigationRuntime,
        result: dict,
    ) -> ActionExecutionRecord:
        completed_at = datetime.now(timezone.utc)
        try:
            self._attempts.complete(attempt, completed_at, result)
            record.status = ActionExecutionStatus.COMPLETED
            record.completed_at = completed_at
            record.result = result
            record.error = None
            record.completion_basis = (
                ActionExecutionCompletionBasis.ACKNOWLEDGED_RESULT
            )
            self._record_terminal(
                proposal, record, runtime, InvestigationEventType.EXECUTION_COMPLETED
            )
        except Exception:
            self._session.rollback()
            raise
        return record

    def fail(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        runtime: InvestigationRuntime,
        error: dict,
        failure_cause: FailureCause,
        outcome_certainty: OutcomeCertainty | None,
    ) -> ActionExecutionRecord:
        completed_at = datetime.now(timezone.utc)
        try:
            self._attempts.fail(
                attempt,
                completed_at,
                error,
                failure_cause,
                outcome_certainty,
            )
            record.status = ActionExecutionStatus.FAILED
            record.completed_at = completed_at
            record.result = None
            record.error = error
            self._record_terminal(
                proposal, record, runtime, InvestigationEventType.EXECUTION_FAILED
            )
        except Exception:
            self._session.rollback()
            raise
        return record

    def mark_outcome_unknown(
        self,
        proposal: ActionProposalRecord,
        record: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        runtime: InvestigationRuntime,
        error: dict,
        failure_cause: FailureCause,
    ) -> ActionExecutionRecord:
        classified_at = datetime.now(timezone.utc)
        try:
            self._attempts.mark_outcome_unknown(
                attempt, classified_at, error, failure_cause
            )
            outcome = self._session.execute(
                update(ActionExecutionRecord)
                .where(
                    ActionExecutionRecord.id == record.id,
                    ActionExecutionRecord.status == ActionExecutionStatus.RUNNING,
                )
                .values(
                    status=ActionExecutionStatus.OUTCOME_UNKNOWN,
                    completed_at=None,
                    result=None,
                    error=error,
                    completion_basis=None,
                )
                .execution_options(synchronize_session="fetch")
            )
            if outcome.rowcount != 1:
                raise InvalidActionExecutionAttemptTransitionError(
                    f"Execution {record.id} is no longer running"
                )
            metadata = self._metadata(proposal, record)
            metadata.update(
                {
                    "attempt_id": attempt.id,
                    "failure_cause": failure_cause.value,
                }
            )
            self._investigations.record_event(
                proposal.investigation_id,
                runtime,
                InvestigationEventType.EXECUTION_ATTEMPT_OUTCOME_UNKNOWN,
                self._investigations.next_event_sequence(proposal.investigation_id),
                commit=False,
                status=ActionExecutionStatus.OUTCOME_UNKNOWN.value,
                metadata=metadata,
            )
            self._commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(record)
        self._session.refresh(attempt)
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
