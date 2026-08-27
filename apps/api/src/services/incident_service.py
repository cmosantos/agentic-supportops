from domain.incident import IncidentCreate, IncidentRead
from repositories.incident_repository import IncidentRepository


class IncidentService:
    def __init__(self, repository: IncidentRepository) -> None:
        self._repository = repository

    def create(self, payload: IncidentCreate) -> IncidentRead:
        return IncidentRead.model_validate(self._repository.create(payload))

    def list_all(self) -> list[IncidentRead]:
        return [IncidentRead.model_validate(item) for item in self._repository.list_all()]

    def get(self, incident_id: int) -> IncidentRead | None:
        incident = self._repository.get(incident_id)
        return IncidentRead.model_validate(incident) if incident else None

