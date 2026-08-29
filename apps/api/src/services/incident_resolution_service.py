from sqlalchemy.exc import IntegrityError

from domain.incident import IncidentStatus
from domain.incident_resolution import (
    IncidentResolutionCreate,
    IncidentResolutionRead,
    ResolutionDecision,
)
from domain.outcome_verification import OutcomeVerificationStatus
from repositories.incident_resolution_repository import (
    IncidentResolutionRepository,
    IncidentResolutionTransitionConflict,
)


class ResolutionIncidentNotFoundError(LookupError):
    pass


class ResolutionVerificationNotFoundError(LookupError):
    pass


class ResolutionOwnershipError(RuntimeError):
    pass


class ResolutionNotEligibleError(RuntimeError):
    pass


class ResolutionDecisionConflictError(RuntimeError):
    pass


class IncidentResolutionService:
    def __init__(self, repository: IncidentResolutionRepository) -> None:
        self._repository = repository

    def decide(
        self, incident_id: int, payload: IncidentResolutionCreate
    ) -> IncidentResolutionRead:
        incident = self._repository.get_incident(incident_id)
        if incident is None:
            raise ResolutionIncidentNotFoundError(f"Incident {incident_id} not found")
        verification = self._repository.get_verification(payload.verification_id)
        if verification is None:
            raise ResolutionVerificationNotFoundError(
                f"Outcome verification {payload.verification_id} not found"
            )
        if verification.incident_id != incident.id:
            raise ResolutionOwnershipError(
                "Outcome verification does not belong to this incident"
            )
        context = self._repository.get_context(verification.id)
        if context is None:
            raise ResolutionOwnershipError("Outcome verification ownership chain is invalid")

        existing = self._repository.get_for_verification(verification.id)
        if existing is not None:
            if existing.decision == payload.decision and existing.reason == payload.reason:
                return IncidentResolutionRead.model_validate(existing)
            raise ResolutionDecisionConflictError(
                "This verification already has a canonical resolution review"
            )

        if incident.status == IncidentStatus.RESOLVED:
            final = self._repository.get_final_for_incident(incident.id)
            if payload.decision == ResolutionDecision.RESOLVE and final is not None:
                return IncidentResolutionRead.model_validate(final)
            raise ResolutionDecisionConflictError("Incident is already resolved")
        if incident.status == IncidentStatus.CLOSED:
            raise ResolutionDecisionConflictError("Closed incident cannot be resolved")
        if verification.status == OutcomeVerificationStatus.RUNNING:
            raise ResolutionNotEligibleError(
                "A running outcome verification cannot be reviewed"
            )
        if (
            payload.decision == ResolutionDecision.RESOLVE
            and verification.status != OutcomeVerificationStatus.VERIFIED
        ):
            raise ResolutionNotEligibleError(
                "Only VERIFIED outcome evidence is eligible for resolution"
            )

        try:
            record = self._repository.decide(context, payload.decision, payload.reason)
        except (IntegrityError, IncidentResolutionTransitionConflict):
            existing = self._repository.get_for_verification(verification.id)
            if existing is None and payload.decision == ResolutionDecision.RESOLVE:
                existing = self._repository.get_final_for_incident(incident.id)
            if existing is None:
                raise
            return IncidentResolutionRead.model_validate(existing)
        return IncidentResolutionRead.model_validate(record)

    def list(self, incident_id: int) -> list[IncidentResolutionRead]:
        if self._repository.get_incident(incident_id) is None:
            raise ResolutionIncidentNotFoundError(f"Incident {incident_id} not found")
        return [
            IncidentResolutionRead.model_validate(record)
            for record in self._repository.list_for_incident(incident_id)
        ]
