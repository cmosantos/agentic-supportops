from db.models import IncidentRecord
from domain.ai import (
    AIInvestigationRead,
    DeterministicInvestigationExecution,
    ProviderUsage,
)
from domain.investigation import (
    EvidenceRead,
    InvestigationGoal,
    InvestigationRead,
    InvestigationStepRead,
)
from observability.tracing import TraceBoundary
from repositories.investigation_repository import InvestigationRepository
from services.investigation_input import (
    build_investigation_goal,
    investigation_goal_trace_attributes,
)
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
        tracing: TraceBoundary | None = None,
    ) -> None:
        self._repository = repository
        self._tools = tools or InvestigationToolRegistry()
        self._tracing = tracing or TraceBoundary()

    def investigate(
        self,
        incident: IncidentRecord,
        goal: InvestigationGoal | None = None,
    ) -> DeterministicInvestigationExecution:
        playbook = PLAYBOOKS.get(incident.catalog_id or "")
        if playbook is None:
            raise UnsupportedInvestigationError(
                f"No deterministic playbook for incident '{incident.catalog_id or incident.id}'"
            )
        resolved_playbook = [
            (step, self._resolve_arguments(step.arguments, incident.investigation_context))
            for step in playbook
        ]
        if goal is None:
            goal = build_investigation_goal(incident)
        attributes = {
            "supportops.incident_reference": incident.catalog_id or str(incident.id),
            "supportops.runtime": "deterministic",
            "supportops.tool.transport": self._tools.transport,
            **investigation_goal_trace_attributes(goal),
        }
        with self._tracing.span("supportops.investigation", attributes) as span:
            run = self._repository.start_ai_run(
                incident.id,
                model="deterministic-playbook",
                mode="deterministic",
                goal_snapshot=goal,
            )
            try:
                for step, arguments in resolved_playbook:
                    result = self._tools.execute(step.tool, arguments)
                    self._repository.record_result(
                        incident.id,
                        result,
                        arguments=arguments,
                        investigation_id=run.id,
                    )
                run = self._repository.complete_deterministic_run(run)
            except Exception as error:
                self._repository.fail_ai_run(
                    run,
                    "deterministic_investigation_failed",
                    str(error),
                    usage=ProviderUsage(runtime="deterministic"),
                )
                raise
            span.set_attribute("supportops.investigation_id", run.id)
            span.set_attribute(
                "supportops.investigation.status",
                run.status.value,
            )
        return DeterministicInvestigationExecution(
            investigation=AIInvestigationRead.model_validate(run),
            incident_id=incident.id,
            catalog_id=incident.catalog_id,
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(
                    incident.id, investigation_id=run.id
                )
            ],
            evidence=[
                EvidenceRead.model_validate(item)
                for item in self._repository.list_evidence(
                    incident.id, investigation_id=run.id
                )
            ],
        )

    def get_investigation(self, incident: IncidentRecord) -> InvestigationRead:
        run = self._repository.get_ai_run(incident.id, mode="deterministic")
        investigation_id = run.id if run is not None else None
        return InvestigationRead(
            incident_id=incident.id,
            catalog_id=incident.catalog_id,
            steps=[
                InvestigationStepRead.model_validate(item)
                for item in self._repository.list_steps(
                    incident.id, investigation_id=investigation_id
                )
            ],
            evidence=[
                EvidenceRead.model_validate(item)
                for item in self._repository.list_evidence(
                    incident.id, investigation_id=investigation_id
                )
            ],
        )

    def get_evidence(self, incident: IncidentRecord) -> list[EvidenceRead]:
        run = self._repository.get_ai_run(incident.id, mode="deterministic")
        investigation_id = run.id if run is not None else None
        return [
            EvidenceRead.model_validate(item)
            for item in self._repository.list_evidence(
                incident.id, investigation_id=investigation_id
            )
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
