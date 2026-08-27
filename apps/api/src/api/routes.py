from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.dependencies import get_db_session
from domain.incident import IncidentCreate, IncidentRead
from repositories.incident_repository import IncidentRepository
from services.incident_service import IncidentService

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
def get_incident(incident_id: int, session: DatabaseSession) -> IncidentRead:
    incident = IncidentService(IncidentRepository(session)).get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "message": "Incident not found"},
        )
    return incident

