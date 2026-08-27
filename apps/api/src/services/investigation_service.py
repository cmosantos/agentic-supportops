from db.models import IncidentRecord
from domain.investigation import EvidenceRead, InvestigationRead, InvestigationStepRead
from repositories.investigation_repository import InvestigationRepository
from services.playbooks import PLAYBOOKS
from services.tool_registry import InvestigationToolRegistry


class UnsupportedInvestigationError(ValueError):
    pass


class InvalidInvestigationContextError(ValueError):
    pass


class InvestigationService:
    def __init__(
        self,
        repository: InvestigationRepository,
        tools: InvestigationToolRegistry | None = None,
    ) -> None:
        self._repository = repository
        self._tools = tools or InvestigationToolRegistry()

    def investigate(self, incident: IncidentRecord) -> InvestigationRead:
        playbook = PLAYBOOKS.get(incident.catalog_id or "")
        if playbook is None:
            raise UnsupportedInvestigationError(
                f"No deterministic playbook for incident '{incident.catalog_id or incident.id}'"
            )
        self._repository.replace_start(incident.id)
        for step in playbook:
            arguments = self._resolve_arguments(step.arguments, incident.investigation_context)
            result = self._tools.execute(step.tool, arguments)
            self._repository.record_result(incident.id, result)
        return self.get_investigation(incident)

    def get_investigation(self, incident: IncidentRecord) -> InvestigationRead:
        return InvestigationRead(
            incident_id=incident.id,
            catalog_id=incident.catalog_id,
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(incident.id)
            ],
            evidence=[
                EvidenceRead.model_validate(item)
                for item in self._repository.list_evidence(incident.id)
            ],
        )

    def get_evidence(self, incident: IncidentRecord) -> list[EvidenceRead]:
        return [
            EvidenceRead.model_validate(item)
            for item in self._repository.list_evidence(incident.id)
        ]

    @staticmethod
    def _resolve_arguments(
        templates: dict[str, str], context: dict[str, str]
    ) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for name, value in templates.items():
            if not value.startswith("$"):
                resolved[name] = value
                continue
            context_key = value[1:]
            if context_key not in context:
                raise InvalidInvestigationContextError(
                    f"Missing investigation context value '{context_key}'"
                )
            resolved[name] = context[context_key]
        return resolved
