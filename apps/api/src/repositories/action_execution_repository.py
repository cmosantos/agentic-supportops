from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionRecord,
    ActionProposalRecord,
    AIInvestigationRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
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


class StaleExecutionPersistenceStatus(StrEnum):
    TRANSITIONED = "transitioned"
    ALREADY_CLASSIFIED = "already_classified"
    NOT_ELIGIBLE = "not_eligible"
    EXECUTION_NOT_FOUND = "execution_not_found"
    ATTEMPT_NOT_FOUND = "attempt_not_found"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    NONCANONICAL_ATTEMPT = "noncanonical_attempt"
    TERMINAL_CONFLICT = "terminal_conflict"
    INCONSISTENT_STATE = "inconsistent_state"
    TRANSITION_CONFLICT = "transition_conflict"


@dataclass(frozen=True)
class StaleExecutionPersistenceResult:
    status: StaleExecutionPersistenceStatus
    execution: ActionExecutionRecord | None
    attempt: ActionExecutionAttemptRecord | None


@dataclass(frozen=True)
class _StaleExecutionContext:
    execution: ActionExecutionRecord
    attempt: ActionExecutionAttemptRecord
    proposal: ActionProposalRecord


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

    def classify_stale_interruption(
        self,
        execution_id: int,
        attempt_id: int,
        cutoff: datetime,
        assessed_at: datetime,
    ) -> StaleExecutionPersistenceResult:
        loaded = self._load_stale_context(execution_id, attempt_id)
        if isinstance(loaded, StaleExecutionPersistenceResult):
            return loaded
        context = loaded
        current = self._describe_stale_context(context, cutoff)
        if current is not None:
            return current

        invocation_started = context.attempt.invocation_started_at is not None
        error = self._interruption_error(invocation_started)
        try:
            if invocation_started:
                attempt_transitioned = self._attempts.classify_stale_after_invocation(
                    context.attempt, cutoff, assessed_at, error
                )
                target_status = ActionExecutionStatus.OUTCOME_UNKNOWN
                outcome_certainty = OutcomeCertainty.UNKNOWN
                assessment_reason = "stale_after_invocation"
            else:
                attempt_transitioned = self._attempts.classify_stale_before_invocation(
                    context.attempt, cutoff, assessed_at, error
                )
                target_status = ActionExecutionStatus.FAILED
                outcome_certainty = OutcomeCertainty.NOT_APPLIED
                assessment_reason = "stale_before_invocation"
            if not attempt_transitioned:
                self._session.rollback()
                return self._reload_after_stale_conflict(
                    execution_id, attempt_id, cutoff
                )
            if not self._transition_stale_aggregate(
                context.execution, target_status, assessed_at, error
            ):
                self._session.rollback()
                return self._reload_after_stale_conflict(
                    execution_id, attempt_id, cutoff
                )
            metadata = self._metadata(context.proposal, context.execution)
            metadata.update(
                {
                    "attempt_id": context.attempt.id,
                    "failure_cause": FailureCause.PROCESS_INTERRUPTED.value,
                    "outcome_certainty": outcome_certainty.value,
                    "assessment_reason": assessment_reason,
                }
            )
            self._investigations.record_event(
                context.proposal.investigation_id,
                self._runtime_for(context.proposal),
                InvestigationEventType.EXECUTION_ATTEMPT_INTERRUPTION_ASSESSED,
                self._investigations.next_event_sequence(
                    context.proposal.investigation_id
                ),
                commit=False,
                status=target_status.value,
                metadata=metadata,
            )
            self._commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(context.execution)
        self._session.refresh(context.attempt)
        return StaleExecutionPersistenceResult(
            StaleExecutionPersistenceStatus.TRANSITIONED,
            context.execution,
            context.attempt,
        )

    def _load_stale_context(
        self, execution_id: int, attempt_id: int
    ) -> _StaleExecutionContext | StaleExecutionPersistenceResult:
        execution = self._session.get(ActionExecutionRecord, execution_id)
        if execution is None:
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.EXECUTION_NOT_FOUND, None, None
            )
        attempt = self._session.get(ActionExecutionAttemptRecord, attempt_id)
        if attempt is None:
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.ATTEMPT_NOT_FOUND, execution, None
            )
        if attempt.execution_id != execution.id:
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.OWNERSHIP_MISMATCH,
                execution,
                attempt,
            )
        canonical = self._attempts.get_canonical_attempt(execution.id)
        if (
            attempt.attempt_number != 1
            or canonical is None
            or canonical.id != attempt.id
            or self._attempts.has_later_attempt(attempt)
        ):
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.NONCANONICAL_ATTEMPT,
                execution,
                attempt,
            )
        proposal = self._session.get(ActionProposalRecord, execution.proposal_id)
        if proposal is None or proposal.incident_id != execution.incident_id:
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.INCONSISTENT_STATE,
                execution,
                attempt,
            )
        return _StaleExecutionContext(execution, attempt, proposal)

    def _describe_stale_context(
        self, context: _StaleExecutionContext, cutoff: datetime
    ) -> StaleExecutionPersistenceResult | None:
        execution = context.execution
        attempt = context.attempt
        if self._is_canonical_interruption(execution, attempt):
            return StaleExecutionPersistenceResult(
                StaleExecutionPersistenceStatus.ALREADY_CLASSIFIED,
                execution,
                attempt,
            )
        if (
            execution.status == ActionExecutionStatus.RUNNING
            and attempt.status == ActionExecutionAttemptStatus.RUNNING
        ):
            stale_reference = attempt.invocation_started_at or attempt.claimed_at
            if self._as_utc(stale_reference) > self._as_utc(cutoff):
                return StaleExecutionPersistenceResult(
                    StaleExecutionPersistenceStatus.NOT_ELIGIBLE,
                    execution,
                    attempt,
                )
            return None
        if self._terminal_states_are_consistent(execution, attempt):
            status = StaleExecutionPersistenceStatus.TERMINAL_CONFLICT
        else:
            status = StaleExecutionPersistenceStatus.INCONSISTENT_STATE
        return StaleExecutionPersistenceResult(status, execution, attempt)

    def _reload_after_stale_conflict(
        self, execution_id: int, attempt_id: int, cutoff: datetime
    ) -> StaleExecutionPersistenceResult:
        loaded = self._load_stale_context(execution_id, attempt_id)
        if isinstance(loaded, StaleExecutionPersistenceResult):
            return loaded
        described = self._describe_stale_context(loaded, cutoff)
        return described or StaleExecutionPersistenceResult(
            StaleExecutionPersistenceStatus.TRANSITION_CONFLICT,
            loaded.execution,
            loaded.attempt,
        )

    def _transition_stale_aggregate(
        self,
        execution: ActionExecutionRecord,
        status: ActionExecutionStatus,
        assessed_at: datetime,
        error: dict,
    ) -> bool:
        outcome = self._session.execute(
            update(ActionExecutionRecord)
            .where(
                ActionExecutionRecord.id == execution.id,
                ActionExecutionRecord.status == ActionExecutionStatus.RUNNING,
            )
            .values(
                status=status,
                completed_at=(
                    assessed_at if status == ActionExecutionStatus.FAILED else None
                ),
                result=None,
                error=error,
                completion_basis=None,
            )
            .execution_options(synchronize_session="fetch")
        )
        return outcome.rowcount == 1

    @staticmethod
    def _is_canonical_interruption(
        execution: ActionExecutionRecord, attempt: ActionExecutionAttemptRecord
    ) -> bool:
        return (
            attempt.failure_cause == FailureCause.PROCESS_INTERRUPTED
            and (
                attempt.status == ActionExecutionAttemptStatus.FAILED
                and attempt.outcome_certainty == OutcomeCertainty.NOT_APPLIED
                and execution.status == ActionExecutionStatus.FAILED
                or attempt.status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
                and attempt.outcome_certainty == OutcomeCertainty.UNKNOWN
                and execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
            )
        )

    @staticmethod
    def _terminal_states_are_consistent(
        execution: ActionExecutionRecord, attempt: ActionExecutionAttemptRecord
    ) -> bool:
        return (
            execution.status == ActionExecutionStatus.COMPLETED
            and attempt.status == ActionExecutionAttemptStatus.COMPLETED
            or execution.status == ActionExecutionStatus.FAILED
            and attempt.status == ActionExecutionAttemptStatus.FAILED
            or execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
            and attempt.status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
        )

    @staticmethod
    def _interruption_error(invocation_started: bool) -> dict[str, str]:
        if invocation_started:
            return {
                "code": "execution_interrupted_after_invocation",
                "message": "Execution was interrupted after capability invocation began",
            }
        return {
            "code": "execution_interrupted_before_invocation",
            "message": "Execution was interrupted before capability invocation began",
        }

    def _runtime_for(self, proposal: ActionProposalRecord) -> InvestigationRuntime:
        investigation = self._session.get(
            AIInvestigationRecord, proposal.investigation_id
        )
        return (
            InvestigationRuntime.AGENTS_SDK
            if investigation is not None and investigation.mode == "agents_sdk"
            else InvestigationRuntime.MANUAL_RESPONSES
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return (
            value.replace(tzinfo=timezone.utc)
            if value.tzinfo is None
            else value.astimezone(timezone.utc)
        )

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
