from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionAttemptRecord,
    ActionExecutionReconciliationRecord,
    ActionExecutionRecord,
    ActionProposalRecord,
    AIInvestigationRecord,
)
from domain.action_execution import (
    ActionExecutionCompletionBasis,
    ActionExecutionStatus,
)
from domain.action_execution_reconciliation import (
    ActionExecutionReconciliationStatus,
)
from domain.ai import InvestigationEventType, InvestigationRuntime
from repositories.investigation_repository import InvestigationRepository


class InvalidActionExecutionReconciliationTransitionError(RuntimeError):
    pass


class ReconciliationRecoveryClaimStatus(StrEnum):
    CLAIMED = "claimed"
    NOT_STALE = "not_stale"
    TERMINAL = "terminal"


class ActionExecutionReconciliationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = InvestigationRepository(session)

    def get_execution(self, execution_id: int) -> ActionExecutionRecord | None:
        return self._session.get(ActionExecutionRecord, execution_id)

    def get_attempt(self, attempt_id: int) -> ActionExecutionAttemptRecord | None:
        return self._session.get(ActionExecutionAttemptRecord, attempt_id)

    def get_canonical_attempt(
        self, execution_id: int
    ) -> ActionExecutionAttemptRecord | None:
        return self._session.scalar(
            select(ActionExecutionAttemptRecord).where(
                ActionExecutionAttemptRecord.execution_id == execution_id,
                ActionExecutionAttemptRecord.attempt_number == 1,
            )
        )

    def get_proposal(self, proposal_id: int) -> ActionProposalRecord | None:
        return self._session.get(ActionProposalRecord, proposal_id)

    def get_for_attempt(
        self, attempt_id: int
    ) -> ActionExecutionReconciliationRecord | None:
        return self._session.scalar(
            select(ActionExecutionReconciliationRecord).where(
                ActionExecutionReconciliationRecord.attempt_id == attempt_id
            )
        )

    def runtime_for(self, proposal: ActionProposalRecord) -> InvestigationRuntime:
        investigation = self._session.get(
            AIInvestigationRecord, proposal.investigation_id
        )
        return (
            InvestigationRuntime.AGENTS_SDK
            if investigation is not None and investigation.mode == "agents_sdk"
            else InvestigationRuntime.MANUAL_RESPONSES
        )

    def start(
        self,
        execution: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        proposal: ActionProposalRecord,
        runtime: InvestigationRuntime,
        observer: str,
        expected_outcome: dict,
    ) -> tuple[ActionExecutionReconciliationRecord, bool]:
        existing = self.get_for_attempt(attempt.id)
        if existing is not None:
            return existing, False

        now = datetime.now(timezone.utc)
        record = ActionExecutionReconciliationRecord(
            attempt_id=attempt.id,
            execution_id=execution.id,
            status=ActionExecutionReconciliationStatus.RUNNING,
            observer=observer,
            expected_outcome=expected_outcome,
            started_at=now,
        )
        self._session.add(record)
        try:
            self._session.flush()
            sequence = self._events.next_event_sequence(proposal.investigation_id)
            metadata = self._metadata(proposal, execution, attempt, record)
            for offset, event_type in enumerate(
                (
                    InvestigationEventType.RECONCILIATION_REQUESTED,
                    InvestigationEventType.RECONCILIATION_STARTED,
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
            existing = self.get_for_attempt(attempt.id)
            if existing is None:
                raise
            return existing, False
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(record)
        return record, True

    def claim_recovery(
        self,
        execution: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        proposal: ActionProposalRecord,
        reconciliation: ActionExecutionReconciliationRecord,
        runtime: InvestigationRuntime,
        cutoff: datetime,
        claimed_at: datetime,
    ) -> tuple[
        ReconciliationRecoveryClaimStatus,
        ActionExecutionReconciliationRecord,
    ]:
        # This timestamp is a SQLite-backed lease, not an exactly-once claim.
        # A crash after the read-only observation becomes recoverable after the
        # next stale window, when observing current state again is safe.
        try:
            claim = self._session.execute(
                update(ActionExecutionReconciliationRecord)
                .where(
                    ActionExecutionReconciliationRecord.id == reconciliation.id,
                    ActionExecutionReconciliationRecord.status
                    == ActionExecutionReconciliationStatus.RUNNING,
                    ActionExecutionReconciliationRecord.started_at <= cutoff,
                )
                .values(started_at=claimed_at)
                .execution_options(synchronize_session="fetch")
            )
            if claim.rowcount != 1:
                self._session.rollback()
                current = self.get_for_attempt(attempt.id)
                if current is None:
                    raise RuntimeError("Canonical reconciliation disappeared")
                status = (
                    ReconciliationRecoveryClaimStatus.TERMINAL
                    if current.status
                    != ActionExecutionReconciliationStatus.RUNNING
                    else ReconciliationRecoveryClaimStatus.NOT_STALE
                )
                return status, current

            sequence = self._events.next_event_sequence(proposal.investigation_id)
            metadata = self._metadata(
                proposal, execution, attempt, reconciliation
            )
            metadata["recovery_claimed_at"] = claimed_at.isoformat()
            for offset, event_type in enumerate(
                (
                    InvestigationEventType.RECONCILIATION_RECOVERY_REQUESTED,
                    InvestigationEventType.RECONCILIATION_RECOVERY_STARTED,
                )
            ):
                self._events.record_event(
                    proposal.investigation_id,
                    runtime,
                    event_type,
                    sequence + offset,
                    commit=False,
                    status=ActionExecutionReconciliationStatus.RUNNING.value,
                    metadata=metadata,
                )
            self._commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(reconciliation)
        return ReconciliationRecoveryClaimStatus.CLAIMED, reconciliation

    def finish(
        self,
        execution: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
        proposal: ActionProposalRecord,
        reconciliation: ActionExecutionReconciliationRecord,
        runtime: InvestigationRuntime,
        status: ActionExecutionReconciliationStatus,
        observed_outcome: dict | None,
        evidence: dict | None,
        error: dict | None,
    ) -> ActionExecutionReconciliationRecord:
        if status == ActionExecutionReconciliationStatus.RUNNING:
            raise ValueError("A reconciliation cannot finish in RUNNING state")

        completed_at = datetime.now(timezone.utc)
        try:
            reconciliation.status = status
            reconciliation.completed_at = completed_at
            reconciliation.observed_outcome = observed_outcome
            reconciliation.evidence = evidence
            reconciliation.error = error

            sequence = self._events.next_event_sequence(proposal.investigation_id)
            metadata = self._metadata(
                proposal, execution, attempt, reconciliation
            )
            metadata["observed_state"] = (
                str(observed_outcome.get("state"))
                if observed_outcome is not None
                else "unavailable"
            )
            self._events.record_event(
                proposal.investigation_id,
                runtime,
                self._terminal_event(status),
                sequence,
                commit=False,
                status=status.value,
                metadata=metadata,
            )

            if status == ActionExecutionReconciliationStatus.DESIRED_STATE_OBSERVED:
                transition = self._session.execute(
                    update(ActionExecutionRecord)
                    .where(
                        ActionExecutionRecord.id == execution.id,
                        ActionExecutionRecord.status
                        == ActionExecutionStatus.OUTCOME_UNKNOWN,
                    )
                    .values(
                        status=ActionExecutionStatus.COMPLETED,
                        completed_at=completed_at,
                        result=None,
                        error=None,
                        completion_basis=(
                            ActionExecutionCompletionBasis.RECONCILIATION
                        ),
                    )
                    .execution_options(synchronize_session="fetch")
                )
                if transition.rowcount != 1:
                    raise InvalidActionExecutionReconciliationTransitionError(
                        f"Execution {execution.id} is no longer outcome unknown"
                    )
                self._events.record_event(
                    proposal.investigation_id,
                    runtime,
                    InvestigationEventType.EXECUTION_COMPLETED,
                    sequence + 1,
                    commit=False,
                    status=ActionExecutionStatus.COMPLETED.value,
                    metadata={
                        **metadata,
                        "completion_basis": (
                            ActionExecutionCompletionBasis.RECONCILIATION.value
                        ),
                    },
                )
            self._commit()
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(reconciliation)
        return reconciliation

    @staticmethod
    def _terminal_event(
        status: ActionExecutionReconciliationStatus,
    ) -> InvestigationEventType:
        return {
            ActionExecutionReconciliationStatus.DESIRED_STATE_OBSERVED: (
                InvestigationEventType.RECONCILIATION_DESIRED_STATE_OBSERVED
            ),
            ActionExecutionReconciliationStatus.UNDESIRED_STATE_OBSERVED: (
                InvestigationEventType.RECONCILIATION_UNDESIRED_STATE_OBSERVED
            ),
            ActionExecutionReconciliationStatus.INCONCLUSIVE: (
                InvestigationEventType.RECONCILIATION_INCONCLUSIVE
            ),
        }[status]

    @staticmethod
    def _metadata(proposal, execution, attempt, reconciliation) -> dict:
        return {
            "incident_id": proposal.incident_id,
            "proposal_id": proposal.id,
            "execution_id": execution.id,
            "attempt_id": attempt.id,
            "reconciliation_id": reconciliation.id,
            "target": proposal.target,
            "observer": reconciliation.observer,
        }

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
