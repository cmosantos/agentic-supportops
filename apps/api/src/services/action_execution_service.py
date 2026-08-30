from db.models import ActionExecutionRecord, ActionProposalRecord
from domain.action_execution import (
    ActionExecutionRead,
    FailureCause,
    OutcomeCertainty,
)
from domain.action_proposal import ApprovalStatus
from domain.ai import InvestigationRuntime
from repositories.action_execution_repository import ActionExecutionRepository
from services.execution_policy import ExecutionPolicy
from services.tool_registry import InvestigationToolRegistry


class ActionExecutionNotFoundError(LookupError):
    pass


class ActionExecutionNotApprovedError(RuntimeError):
    pass


class ActionExecutionService:
    def __init__(
        self,
        repository: ActionExecutionRepository,
        policy: ExecutionPolicy,
        tools: InvestigationToolRegistry,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._tools = tools

    def execute(
        self,
        incident_id: int,
        investigation_id: int,
        proposal_id: int,
        runtime: InvestigationRuntime,
    ) -> ActionExecutionRead:
        proposal = self._repository.get_proposal(
            incident_id, investigation_id, proposal_id
        )
        if proposal is None:
            raise ActionExecutionNotFoundError(
                f"Action proposal {proposal_id} not found"
            )
        if proposal.approval_status != ApprovalStatus.APPROVED:
            raise ActionExecutionNotApprovedError(
                "Only an approved action proposal can execute"
            )

        capability_name = proposal.action_type.value
        self._policy.authorize(capability_name)
        execution, attempt, created = self._repository.start(proposal, runtime)
        if not created:
            return ActionExecutionRead.model_validate(execution)
        if attempt is None:
            raise RuntimeError("First execution attempt was not persisted")

        arguments = self._persisted_arguments(proposal)
        try:
            tool_result = self._tools.execute(capability_name, arguments)
        except Exception:
            execution = self._repository.fail(
                proposal,
                execution,
                attempt,
                runtime,
                {
                    "code": "capability_failure",
                    "message": "Controlled capability failed",
                },
                FailureCause.TOOL_EXCEPTION,
                None,
            )
            return ActionExecutionRead.model_validate(execution)

        if tool_result.success:
            execution = self._repository.complete(
                proposal,
                execution,
                attempt,
                runtime,
                tool_result.model_dump(mode="json"),
            )
        else:
            error = (
                tool_result.error.model_dump(mode="json")
                if tool_result.error
                else {
                    "code": "capability_failure",
                    "message": "Controlled capability failed",
                }
            )
            safe_pre_mutation_failure = error["code"] in {
                "application_not_found",
                "service_not_found",
            }
            execution = self._repository.fail(
                proposal,
                execution,
                attempt,
                runtime,
                error,
                FailureCause.TOOL_REJECTED,
                OutcomeCertainty.NOT_APPLIED
                if safe_pre_mutation_failure
                else None,
            )
        return ActionExecutionRead.model_validate(execution)

    @staticmethod
    def _persisted_arguments(proposal: ActionProposalRecord) -> dict[str, str]:
        return {"target": proposal.target, **proposal.parameters}
