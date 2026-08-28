from db.models import ActionProposalRecord, AIInvestigationRecord
from domain.action_proposal import (
    ActionProposalCreate,
    ActionProposalRead,
    ApprovalStatus,
)
from domain.ai import AIInvestigationResult, AIInvestigationStatus, InvestigationRuntime
from domain.investigation import InvestigationOrigin
from repositories.action_proposal_repository import ActionProposalRepository
from repositories.investigation_repository import InvestigationRepository
from services.action_policy import (
    AllowedActionRegistry,
    InvalidActionProposalError,
    InvalidActionTypeError,
)


class ActionProposalEligibilityError(RuntimeError):
    pass


class ActionProposalEvidenceError(RuntimeError):
    pass


class ActionProposalService:
    def __init__(
        self,
        proposals: ActionProposalRepository,
        investigations: InvestigationRepository,
        policy: AllowedActionRegistry | None = None,
    ) -> None:
        self._proposals = proposals
        self._investigations = investigations
        self._policy = policy or AllowedActionRegistry()

    def create(
        self,
        investigation: AIInvestigationRecord,
        proposal: ActionProposalCreate,
    ) -> ActionProposalRead:
        result = self._eligible_result(investigation)
        action_type = self._policy.validate(proposal)
        origin, runtime = self._boundaries(investigation)
        persisted_evidence_ids = {
            item.id
            for item in self._investigations.list_evidence(
                investigation.incident_id, origin, investigation.id
            )
        }
        requested = set(proposal.supporting_evidence_ids)
        if (
            not requested.issubset(persisted_evidence_ids)
            or not requested.issubset(result.evidence_ids)
        ):
            raise ActionProposalEvidenceError(
                "Supporting evidence must belong to the originating investigation"
            )
        record = self._proposals.create(
            investigation.id,
            investigation.incident_id,
            action_type,
            proposal,
            runtime,
        )
        return ActionProposalRead.model_validate(record)

    def list(self, investigation: AIInvestigationRecord) -> list[ActionProposalRead]:
        return [
            ActionProposalRead.model_validate(item)
            for item in self._proposals.list_for_investigation(investigation.id)
        ]

    def approve(
        self, investigation: AIInvestigationRecord, proposal_id: int
    ) -> ActionProposalRead:
        record = self._proposal_or_error(investigation, proposal_id)
        _, runtime = self._boundaries(investigation)
        return ActionProposalRead.model_validate(
            self._proposals.decide(record, ApprovalStatus.APPROVED, runtime)
        )

    def reject(
        self,
        investigation: AIInvestigationRecord,
        proposal_id: int,
        reason: str,
    ) -> ActionProposalRead:
        record = self._proposal_or_error(investigation, proposal_id)
        _, runtime = self._boundaries(investigation)
        return ActionProposalRead.model_validate(
            self._proposals.decide(
                record, ApprovalStatus.REJECTED, runtime, rejection_reason=reason
            )
        )

    @staticmethod
    def _eligible_result(
        investigation: AIInvestigationRecord,
    ) -> AIInvestigationResult:
        if investigation.status != AIInvestigationStatus.COMPLETED:
            raise ActionProposalEligibilityError(
                "Only a completed investigation can create an action proposal"
            )
        result = AIInvestigationResult.model_validate(investigation.result)
        if not result.human_action_required:
            raise ActionProposalEligibilityError(
                "Investigation does not require human action"
            )
        return result

    def _proposal_or_error(
        self, investigation: AIInvestigationRecord, proposal_id: int
    ) -> ActionProposalRecord:
        record = self._proposals.get(investigation.id, proposal_id)
        if record is None:
            raise ActionProposalNotFoundError(proposal_id)
        return record

    @staticmethod
    def _boundaries(
        investigation: AIInvestigationRecord,
    ) -> tuple[InvestigationOrigin, InvestigationRuntime]:
        if investigation.mode == "agents_sdk":
            return InvestigationOrigin.AGENTS_SDK, InvestigationRuntime.AGENTS_SDK
        return InvestigationOrigin.AI, InvestigationRuntime.MANUAL_RESPONSES


class ActionProposalNotFoundError(RuntimeError):
    def __init__(self, proposal_id: int) -> None:
        super().__init__(f"Action proposal {proposal_id} not found")


__all__ = [
    "ActionProposalEligibilityError",
    "ActionProposalEvidenceError",
    "ActionProposalNotFoundError",
    "ActionProposalService",
    "InvalidActionProposalError",
    "InvalidActionTypeError",
]
