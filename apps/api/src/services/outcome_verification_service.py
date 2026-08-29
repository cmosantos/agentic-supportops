from domain.action_execution import ActionExecutionStatus
from domain.outcome_verification import OutcomeVerificationRead, OutcomeVerificationStatus
from repositories.outcome_verification_repository import OutcomeVerificationRepository
from services.tool_registry import InvestigationToolRegistry
from services.verification_policy import VerificationPolicy


class ActionExecutionNotFoundError(LookupError):
    pass


class ActionExecutionNotCompletedError(RuntimeError):
    pass


class OutcomeVerificationNotFoundError(LookupError):
    pass


class OutcomeVerificationService:
    def __init__(
        self,
        repository: OutcomeVerificationRepository,
        policy: VerificationPolicy,
        tools: InvestigationToolRegistry,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._tools = tools

    def verify(self, execution_id: int) -> OutcomeVerificationRead:
        context = self._repository.get_execution_and_proposal(execution_id)
        if context is None:
            raise ActionExecutionNotFoundError(f"Action execution {execution_id} not found")
        execution, proposal = context
        if execution.status != ActionExecutionStatus.COMPLETED:
            raise ActionExecutionNotCompletedError(
                "Only a completed action execution can be verified"
            )
        strategy = self._policy.strategy_for(execution.capability_name)
        runtime = self._repository.runtime_for(proposal)
        expected = {"state": strategy.expected_state}
        verification, created = self._repository.start(
            execution, proposal, runtime, expected
        )
        if not created:
            return OutcomeVerificationRead.model_validate(verification)

        try:
            observation = self._tools.execute(
                strategy.observer, {"application_id": proposal.target}
            )
        except Exception:
            verification = self._repository.finish(
                execution,
                proposal,
                verification,
                runtime,
                OutcomeVerificationStatus.FAILED,
                None,
                None,
                {
                    "code": "observer_failure",
                    "message": "Unable to collect reliable post-execution evidence",
                },
            )
            return OutcomeVerificationRead.model_validate(verification)

        if not observation.success or observation.data is None:
            safe_message = (
                observation.error.message
                if observation.error is not None
                else "Unable to collect reliable post-execution evidence"
            )
            verification = self._repository.finish(
                execution,
                proposal,
                verification,
                runtime,
                OutcomeVerificationStatus.FAILED,
                None,
                None,
                {"code": "observer_failure", "message": safe_message},
            )
            return OutcomeVerificationRead.model_validate(verification)

        observed_state = str(observation.data.get("status", "unknown")).casefold()
        observed = {"state": observed_state}
        evidence = {
            "target": proposal.target,
            "observer": strategy.observer,
            "expected_state": strategy.expected_state,
            "observed_state": observed_state,
        }
        status = (
            OutcomeVerificationStatus.VERIFIED
            if observed_state == strategy.expected_state
            else OutcomeVerificationStatus.NOT_VERIFIED
        )
        verification = self._repository.finish(
            execution,
            proposal,
            verification,
            runtime,
            status,
            observed,
            evidence,
            None,
        )
        return OutcomeVerificationRead.model_validate(verification)

    def get(self, execution_id: int) -> OutcomeVerificationRead:
        record = self._repository.get_for_execution(execution_id)
        if record is None:
            raise OutcomeVerificationNotFoundError(
                f"Outcome verification for execution {execution_id} not found"
            )
        return OutcomeVerificationRead.model_validate(record)
