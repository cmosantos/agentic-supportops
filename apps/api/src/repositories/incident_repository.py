from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import IncidentRecord
from domain.incident import IncidentCreate, IncidentStatus


class IncidentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, payload: IncidentCreate) -> IncidentRecord:
        incident = IncidentRecord(
            **payload.model_dump(), status=IncidentStatus.OPEN
        )
        self._session.add(incident)
        self._session.commit()
        self._session.refresh(incident)
        return incident

    def list_all(self) -> list[IncidentRecord]:
        statement = select(IncidentRecord).order_by(IncidentRecord.created_at, IncidentRecord.id)
        return list(self._session.scalars(statement))

    def get(self, incident_id: int) -> IncidentRecord | None:
        return self._session.get(IncidentRecord, incident_id)

