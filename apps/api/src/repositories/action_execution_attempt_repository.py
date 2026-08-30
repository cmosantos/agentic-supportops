from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import ActionExecutionAttemptRecord
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    FailureCause,
    OutcomeCertainty,
)


class InvalidActionExecutionAttemptTransitionError(RuntimeError):
    pass


class ActionExecutionAttemptRepository:
    """Persists physical invocation history inside the caller's transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create_first_attempt(
        self, execution_id: int, claimed_at: datetime
    ) -> ActionExecutionAttemptRecord:
        record = ActionExecutionAttemptRecord(
            execution_id=execution_id,
            attempt_number=1,
            status=ActionExecutionAttemptStatus.RUNNING,
            claimed_at=claimed_at,
        )
        self._session.add(record)
        self._session.flush()
        return record

    def get_canonical_attempt(
        self, execution_id: int
    ) -> ActionExecutionAttemptRecord | None:
        return self._session.scalar(
            select(ActionExecutionAttemptRecord).where(
                ActionExecutionAttemptRecord.execution_id == execution_id,
                ActionExecutionAttemptRecord.attempt_number == 1,
            )
        )

    def mark_invocation_started(
        self, record: ActionExecutionAttemptRecord, started_at: datetime
    ) -> None:
        outcome = self._session.execute(
            update(ActionExecutionAttemptRecord)
            .where(
                ActionExecutionAttemptRecord.id == record.id,
                ActionExecutionAttemptRecord.status
                == ActionExecutionAttemptStatus.RUNNING,
                ActionExecutionAttemptRecord.invocation_started_at.is_(None),
            )
            .values(invocation_started_at=started_at)
            .execution_options(synchronize_session="fetch")
        )
        if outcome.rowcount != 1:
            raise InvalidActionExecutionAttemptTransitionError(
                f"Attempt {record.id} cannot enter invocation twice"
            )

    def complete(
        self,
        record: ActionExecutionAttemptRecord,
        completed_at: datetime,
        result: dict,
    ) -> None:
        self._terminalize(
            record,
            status=ActionExecutionAttemptStatus.COMPLETED,
            completed_at=completed_at,
            result=result,
            error=None,
            failure_cause=None,
            outcome_certainty=OutcomeCertainty.APPLIED_ACKNOWLEDGED,
        )

    def fail(
        self,
        record: ActionExecutionAttemptRecord,
        completed_at: datetime,
        error: dict,
        failure_cause: FailureCause,
        outcome_certainty: OutcomeCertainty | None,
    ) -> None:
        self._terminalize(
            record,
            status=ActionExecutionAttemptStatus.FAILED,
            completed_at=completed_at,
            result=None,
            error=error,
            failure_cause=failure_cause,
            outcome_certainty=outcome_certainty,
        )

    def mark_outcome_unknown(
        self,
        record: ActionExecutionAttemptRecord,
        completed_at: datetime,
        error: dict,
        failure_cause: FailureCause,
    ) -> None:
        self._terminalize(
            record,
            status=ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            completed_at=completed_at,
            result=None,
            error=error,
            failure_cause=failure_cause,
            outcome_certainty=OutcomeCertainty.UNKNOWN,
        )

    def _terminalize(
        self,
        record: ActionExecutionAttemptRecord,
        *,
        status: ActionExecutionAttemptStatus,
        completed_at: datetime,
        result: dict | None,
        error: dict | None,
        failure_cause: FailureCause | None,
        outcome_certainty: OutcomeCertainty | None,
    ) -> None:
        outcome = self._session.execute(
            update(ActionExecutionAttemptRecord)
            .where(
                ActionExecutionAttemptRecord.id == record.id,
                ActionExecutionAttemptRecord.status
                == ActionExecutionAttemptStatus.RUNNING,
            )
            .values(
                status=status,
                completed_at=completed_at,
                result=result,
                error=error,
                failure_cause=failure_cause,
                outcome_certainty=outcome_certainty,
            )
            .execution_options(synchronize_session="fetch")
        )
        if outcome.rowcount != 1:
            raise InvalidActionExecutionAttemptTransitionError(
                f"Attempt {record.id} is no longer running"
            )
