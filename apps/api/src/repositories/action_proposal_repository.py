from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import ActionProposalRecord
from domain.action_proposal import (
    ActionProposalCreate,
    ActionType,
    ApprovalStatus,
)
from domain.ai import InvestigationEventType, InvestigationRuntime
from repositories.investigation_repository import InvestigationRepository


class ActionProposalRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._investigations = InvestigationRepository(session)

    def create(
        self,
        investigation_id: int,
        incident_id: int,
        action_type: ActionType,
        proposal: ActionProposalCreate,
        runtime: InvestigationRuntime,
    ) -> ActionProposalRecord:
        record = ActionProposalRecord(
            investigation_id=investigation_id,
            incident_id=incident_id,
            action_type=action_type,
            target=proposal.target,
            parameters=proposal.parameters,
            rationale=proposal.rationale,
            supporting_evidence_ids=proposal.supporting_evidence_ids,
            risk_level=proposal.risk_level,
            approval_status=ApprovalStatus.PENDING,
        )
        self._session.add(record)
        self._session.flush()
        self._investigations.record_event(
            investigation_id,
            runtime,
            InvestigationEventType.ACTION_PROPOSAL_CREATED,
            self._investigations.next_event_sequence(investigation_id),
            commit=False,
            status=ApprovalStatus.PENDING.value,
            metadata={
                "proposal_id": record.id,
                "action_type": action_type.value,
            },
        )
        self._commit()
        self._session.refresh(record)
        return record

    def list_for_investigation(
        self, investigation_id: int
    ) -> list[ActionProposalRecord]:
        return list(
            self._session.scalars(
                select(ActionProposalRecord)
                .where(ActionProposalRecord.investigation_id == investigation_id)
                .order_by(ActionProposalRecord.id)
            )
        )

    def get(
        self, investigation_id: int, proposal_id: int
    ) -> ActionProposalRecord | None:
        return self._session.scalar(
            select(ActionProposalRecord).where(
                ActionProposalRecord.id == proposal_id,
                ActionProposalRecord.investigation_id == investigation_id,
            )
        )

    def decide(
        self,
        record: ActionProposalRecord,
        status: ApprovalStatus,
        runtime: InvestigationRuntime,
        rejection_reason: str | None = None,
    ) -> ActionProposalRecord:
        if record.approval_status != ApprovalStatus.PENDING:
            raise InvalidApprovalTransitionError(record.id, record.approval_status)
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise InvalidApprovalTransitionError(record.id, record.approval_status)

        decision_at = datetime.now(timezone.utc)
        transition = self._session.execute(
            update(ActionProposalRecord)
            .where(
                ActionProposalRecord.id == record.id,
                ActionProposalRecord.approval_status == ApprovalStatus.PENDING,
            )
            .values(
                approval_status=status,
                decision_at=decision_at,
                rejection_reason=rejection_reason,
            )
        )
        if transition.rowcount != 1:
            self._session.rollback()
            self._session.refresh(record)
            raise InvalidApprovalTransitionError(record.id, record.approval_status)
        event_type = (
            InvestigationEventType.ACTION_PROPOSAL_APPROVED
            if status == ApprovalStatus.APPROVED
            else InvestigationEventType.ACTION_PROPOSAL_REJECTED
        )
        metadata = {"proposal_id": record.id}
        if rejection_reason is not None:
            metadata["rejection_reason"] = rejection_reason
        self._investigations.record_event(
            record.investigation_id,
            runtime,
            event_type,
            self._investigations.next_event_sequence(record.investigation_id),
            commit=False,
            status=status.value,
            metadata=metadata,
        )
        self._commit()
        self._session.refresh(record)
        return record

    def _commit(self) -> None:
        try:
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise


class InvalidApprovalTransitionError(RuntimeError):
    def __init__(self, proposal_id: int, status: ApprovalStatus) -> None:
        super().__init__(
            f"Action proposal {proposal_id} was already decided as {status.value}"
        )
