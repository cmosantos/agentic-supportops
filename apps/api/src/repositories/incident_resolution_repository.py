from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    ActionExecutionRecord,
    ActionProposalRecord,
    AIInvestigationRecord,
    IncidentRecord,
    IncidentResolutionDecisionRecord,
    OutcomeVerificationRecord,
)
from domain.ai import InvestigationEventType, InvestigationRuntime
from domain.incident import IncidentStatus
from domain.incident_resolution import ResolutionDecision
from repositories.investigation_repository import InvestigationRepository


ResolutionContext = tuple[
    OutcomeVerificationRecord,
    ActionExecutionRecord,
    ActionProposalRecord,
    IncidentRecord,
]


class IncidentResolutionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._events = InvestigationRepository(session)

    def get_incident(self, incident_id: int) -> IncidentRecord | None:
        return self._session.get(IncidentRecord, incident_id)

    def get_verification(self, verification_id: int) -> OutcomeVerificationRecord | None:
        return self._session.get(OutcomeVerificationRecord, verification_id)

    def get_context(self, verification_id: int) -> ResolutionContext | None:
        row = self._session.execute(
            select(
                OutcomeVerificationRecord,
                ActionExecutionRecord,
                ActionProposalRecord,
                IncidentRecord,
            )
            .join(
                ActionExecutionRecord,
                ActionExecutionRecord.id == OutcomeVerificationRecord.execution_id,
            )
            .join(
                ActionProposalRecord,
                ActionProposalRecord.id == ActionExecutionRecord.proposal_id,
            )
            .join(IncidentRecord, IncidentRecord.id == ActionProposalRecord.incident_id)
            .where(
                OutcomeVerificationRecord.id == verification_id,
                OutcomeVerificationRecord.proposal_id == ActionProposalRecord.id,
                OutcomeVerificationRecord.incident_id == IncidentRecord.id,
                ActionExecutionRecord.incident_id == IncidentRecord.id,
            )
        ).one_or_none()
        return tuple(row) if row is not None else None

    def get_for_verification(
        self, verification_id: int
    ) -> IncidentResolutionDecisionRecord | None:
        return self._session.scalar(
            select(IncidentResolutionDecisionRecord).where(
                IncidentResolutionDecisionRecord.verification_id == verification_id
            )
        )

    def get_final_for_incident(
        self, incident_id: int
    ) -> IncidentResolutionDecisionRecord | None:
        return self._session.scalar(
            select(IncidentResolutionDecisionRecord).where(
                IncidentResolutionDecisionRecord.incident_id == incident_id,
                IncidentResolutionDecisionRecord.decision == ResolutionDecision.RESOLVE,
            )
        )

    def list_for_incident(
        self, incident_id: int
    ) -> list[IncidentResolutionDecisionRecord]:
        return list(
            self._session.scalars(
                select(IncidentResolutionDecisionRecord)
                .where(IncidentResolutionDecisionRecord.incident_id == incident_id)
                .order_by(IncidentResolutionDecisionRecord.id)
            )
        )

    def decide(
        self,
        context: ResolutionContext,
        decision: ResolutionDecision,
        reason: str | None,
    ) -> IncidentResolutionDecisionRecord:
        verification, execution, proposal, incident = context
        record = IncidentResolutionDecisionRecord(
            incident_id=incident.id,
            verification_id=verification.id,
            execution_id=execution.id,
            proposal_id=proposal.id,
            decision=decision,
            reason=reason,
            decided_at=datetime.now(timezone.utc),
        )
        self._session.add(record)
        try:
            self._session.flush()
            if decision == ResolutionDecision.RESOLVE:
                transition = self._session.execute(
                    update(IncidentRecord)
                    .where(
                        IncidentRecord.id == incident.id,
                        IncidentRecord.status.not_in(
                            {IncidentStatus.RESOLVED, IncidentStatus.CLOSED}
                        ),
                    )
                    .values(status=IncidentStatus.RESOLVED)
                )
                if transition.rowcount != 1:
                    raise IncidentResolutionTransitionConflict()

            investigation = self._session.get(
                AIInvestigationRecord, proposal.investigation_id
            )
            runtime = (
                InvestigationRuntime.AGENTS_SDK
                if investigation is not None and investigation.mode == "agents_sdk"
                else InvestigationRuntime.MANUAL_RESPONSES
            )
            sequence = self._events.next_event_sequence(proposal.investigation_id)
            metadata = self._metadata(record)
            self._events.record_event(
                proposal.investigation_id,
                runtime,
                InvestigationEventType.RESOLUTION_REVIEWED,
                sequence,
                commit=False,
                status=decision.value,
                metadata=metadata,
            )
            terminal_event = (
                InvestigationEventType.INCIDENT_RESOLVED
                if decision == ResolutionDecision.RESOLVE
                else InvestigationEventType.INCIDENT_KEPT_OPEN
            )
            self._events.record_event(
                proposal.investigation_id,
                runtime,
                terminal_event,
                sequence + 1,
                commit=False,
                status=(
                    IncidentStatus.RESOLVED.value
                    if decision == ResolutionDecision.RESOLVE
                    else incident.status.value
                ),
                metadata=metadata,
            )
            self._session.commit()
        except (IntegrityError, IncidentResolutionTransitionConflict):
            self._session.rollback()
            raise
        except Exception:
            self._session.rollback()
            raise
        self._session.refresh(record)
        return record

    @staticmethod
    def _metadata(record: IncidentResolutionDecisionRecord) -> dict[str, int | str]:
        return {
            "incident_id": record.incident_id,
            "verification_id": record.verification_id,
            "execution_id": record.execution_id,
            "proposal_id": record.proposal_id,
            "resolution_decision_id": record.id,
            "decision": record.decision.value,
        }


class IncidentResolutionTransitionConflict(RuntimeError):
    pass
