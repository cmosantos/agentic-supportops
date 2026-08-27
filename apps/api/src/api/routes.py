from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.dependencies import get_db_session
from db.models import IncidentRecord
from domain.incident import IncidentCreate, IncidentRead
from domain.investigation import EvidenceRead, InvestigationRead
from repositories.incident_repository import IncidentRepository
from repositories.investigation_repository import InvestigationRepository
from services.incident_service import IncidentService
from services.investigation_service import (
    InvalidInvestigationContextError,
    InvestigationService,
    UnsupportedInvestigationError,
)

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db_session)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agentic-supportops-api"}


@router.post("/incidents", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, session: DatabaseSession) -> IncidentRead:
    return IncidentService(IncidentRepository(session)).create(payload)


@router.get("/incidents", response_model=list[IncidentRead])
def list_incidents(session: DatabaseSession) -> list[IncidentRead]:
    return IncidentService(IncidentRepository(session)).list_all()


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentRead,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Incident not found"}},
)
def get_incident(incident_id: str, session: DatabaseSession) -> IncidentRead:
    incident = _incident_or_404(incident_id, session)
    return IncidentRead.model_validate(incident)


@router.post("/incidents/{incident_id}/investigate", response_model=InvestigationRead)
def investigate_incident(incident_id: str, session: DatabaseSession) -> InvestigationRead:
    incident = _incident_or_404(incident_id, session)
    service = InvestigationService(InvestigationRepository(session))
    try:
        return service.investigate(incident)
    except (UnsupportedInvestigationError, InvalidInvestigationContextError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "investigation_not_supported", "message": str(error)},
        ) from error


@router.get("/incidents/{incident_id}/evidence", response_model=list[EvidenceRead])
def get_incident_evidence(incident_id: str, session: DatabaseSession) -> list[EvidenceRead]:
    incident = _incident_or_404(incident_id, session)
    return InvestigationService(InvestigationRepository(session)).get_evidence(incident)


@router.get("/incidents/{incident_id}/investigation", response_model=InvestigationRead)
def get_incident_investigation(
    incident_id: str, session: DatabaseSession
) -> InvestigationRead:
    incident = _incident_or_404(incident_id, session)
    return InvestigationService(InvestigationRepository(session)).get_investigation(incident)


def _incident_or_404(incident_id: str, session: Session) -> IncidentRecord:
    incident = IncidentRepository(session).get_by_reference(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "message": "Incident not found"},
        )
    return incident
