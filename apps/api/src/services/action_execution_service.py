from db.models import ActionExecutionRecord, ActionProposalRecord
from domain.action_execution import (
    ActionExecutionRead,
    FailureCause,
    OutcomeCertainty,
)
from domain.action_proposal import ApprovalStatus
from domain.ai import InvestigationRuntime
from domain.investigation import ToolResult
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

        attempt = self._repository.mark_invocation_started(attempt)
        arguments = self._persisted_arguments(proposal)
        try:
            tool_result = self._tools.execute(capability_name, arguments)
        except TimeoutError:
            execution = self._repository.mark_outcome_unknown(
                proposal,
                execution,
                attempt,
                runtime,
                {
                    "code": "capability_timeout",
                    "message": "Controlled capability timed out with an unknown outcome",
                },
                FailureCause.TIMEOUT,
            )
            return ActionExecutionRead.model_validate(execution)
        except Exception:
            execution = self._repository.mark_outcome_unknown(
                proposal,
                execution,
                attempt,
                runtime,
                {
                    "code": "capability_outcome_unknown",
                    "message": "Controlled capability outcome could not be acknowledged",
                },
                FailureCause.TOOL_EXCEPTION,
            )
            return ActionExecutionRead.model_validate(execution)

        if not self._is_valid_tool_result(tool_result):
            execution = self._repository.mark_outcome_unknown(
                proposal,
                execution,
                attempt,
                runtime,
                {
                    "code": "capability_result_invalid",
                    "message": "Controlled capability returned an invalid result",
                },
                FailureCause.RESULT_INVALID,
            )
            return ActionExecutionRead.model_validate(execution)

        if tool_result.success:
            execution = self._persist_acknowledged_result(
                proposal, execution, attempt, runtime, tool_result
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
            if safe_pre_mutation_failure:
                execution = self._persist_known_rejection(
                    proposal, execution, attempt, runtime, error
                )
            else:
                execution = self._repository.mark_outcome_unknown(
                    proposal,
                    execution,
                    attempt,
                    runtime,
                    {
                        "code": "capability_result_invalid",
                        "message": "Controlled capability returned an untrusted failure result",
                    },
                    FailureCause.RESULT_INVALID,
                )
        return ActionExecutionRead.model_validate(execution)

    def _persist_acknowledged_result(
        self, proposal, execution, attempt, runtime, tool_result: ToolResult
    ) -> ActionExecutionRecord:
        try:
            return self._repository.complete(
                proposal,
                execution,
                attempt,
                runtime,
                tool_result.model_dump(mode="json"),
            )
        except Exception:
            return self._classify_terminal_persistence_failure(
                proposal, execution, attempt, runtime
            )

    def _persist_known_rejection(
        self, proposal, execution, attempt, runtime, error: dict
    ) -> ActionExecutionRecord:
        try:
            return self._repository.fail(
                proposal,
                execution,
                attempt,
                runtime,
                error,
                FailureCause.TOOL_REJECTED,
                OutcomeCertainty.NOT_APPLIED,
            )
        except Exception:
            return self._classify_terminal_persistence_failure(
                proposal, execution, attempt, runtime
            )

    def _classify_terminal_persistence_failure(
        self, proposal, execution, attempt, runtime
    ) -> ActionExecutionRecord:
        return self._repository.mark_outcome_unknown(
            proposal,
            execution,
            attempt,
            runtime,
            {
                "code": "terminal_persistence_failed",
                "message": "Execution result could not be durably acknowledged",
            },
            FailureCause.TERMINAL_PERSISTENCE_FAILED,
        )

    @staticmethod
    def _is_valid_tool_result(result: object) -> bool:
        if not isinstance(result, ToolResult):
            return False
        if result.success:
            return result.data is not None and result.error is None
        return result.error is not None

    @staticmethod
    def _persisted_arguments(proposal: ActionProposalRecord) -> dict[str, str]:
        return {"target": proposal.target, **proposal.parameters}
