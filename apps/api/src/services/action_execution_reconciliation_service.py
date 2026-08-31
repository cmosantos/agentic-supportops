from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from db.models import ActionExecutionAttemptRecord, ActionExecutionRecord
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionStatus,
    OutcomeCertainty,
)
from domain.action_execution_reconciliation import (
    ActionExecutionReconciliationOperationalRead,
    ActionExecutionReconciliationRead,
    ActionExecutionReconciliationStatus,
    ReconciliationRecoveryBlockReason,
    reconciliation_recovery_block_reason,
)
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
    ReconciliationRecoveryClaimStatus,
)
from services.tool_registry import InvestigationToolRegistry
from services.verification_policy import (
    VerificationPolicy,
    VerificationPolicyDeniedError,
)


class ActionExecutionReconciliationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ActionExecutionReconciliationService:
    def __init__(
        self,
        repository: ActionExecutionReconciliationRepository,
        policy: VerificationPolicy,
        tools: InvestigationToolRegistry | None = None,
        reconciliation_stale_after_seconds: int = 300,
        current_time: Callable[[], datetime] | None = None,
    ) -> None:
        if reconciliation_stale_after_seconds <= 0:
            raise ValueError(
                "reconciliation_stale_after_seconds must be greater than zero"
            )
        self._repository = repository
        self._policy = policy
        self._tools = tools
        self._reconciliation_stale_after_seconds = (
            reconciliation_stale_after_seconds
        )
        self._current_time = current_time or self._utc_now

    def get_operational_view(
        self, execution_id: int, attempt_id: int
    ) -> ActionExecutionReconciliationOperationalRead:
        execution = self._repository.get_execution(execution_id)
        if execution is None:
            self._raise("action_execution_not_found", "Action execution not found")
        attempt = self._repository.get_attempt(attempt_id)
        if attempt is None:
            self._raise("execution_attempt_not_found", "Execution attempt not found")
        if attempt.execution_id != execution.id:
            self._raise(
                "execution_attempt_mismatch",
                "Execution attempt does not belong to this execution",
            )
        reconciliation = self._repository.get_for_attempt(attempt.id)
        if reconciliation is None:
            self._raise(
                "action_execution_reconciliation_not_found",
                "Canonical action execution reconciliation not found",
            )
        if reconciliation.execution_id != execution.id:
            self._raise(
                "execution_reconciliation_mismatch",
                "Reconciliation does not belong to this execution",
            )

        now = self._as_utc(self._current_time())
        cutoff = now - timedelta(
            seconds=self._reconciliation_stale_after_seconds
        )
        is_stale = (
            reconciliation.status
            == ActionExecutionReconciliationStatus.RUNNING
            and self._stored_as_utc(reconciliation.started_at) <= cutoff
        )
        reason, _, _ = self._recovery_block_reason(
            execution, attempt, reconciliation, is_stale
        )
        recoverable = reason is None
        persisted = ActionExecutionReconciliationRead.model_validate(
            reconciliation
        )
        return ActionExecutionReconciliationOperationalRead(
            **persisted.model_dump(),
            is_stale=is_stale,
            recoverable=recoverable,
            recovery_block_reason=reason,
        )

    def reconcile(
        self, execution_id: int, attempt_id: int
    ) -> ActionExecutionReconciliationRead:
        execution, attempt = self._load_canonical_context(
            execution_id, attempt_id
        )
        existing = self._repository.get_for_attempt(attempt.id)
        if existing is not None:
            return ActionExecutionReconciliationRead.model_validate(existing)

        self._validate_unknown_context(execution, attempt)
        proposal = self._load_proposal(execution)
        strategy = self._policy.strategy_for(execution.capability_name)
        runtime = self._repository.runtime_for(proposal)
        reconciliation, created = self._repository.start(
            execution,
            attempt,
            proposal,
            runtime,
            strategy.observer,
            {"state": strategy.expected_state},
        )
        if not created:
            return ActionExecutionReconciliationRead.model_validate(reconciliation)
        return self._observe_and_finish(
            execution,
            attempt,
            proposal,
            reconciliation,
            runtime,
            strategy,
        )

    def recover(
        self, execution_id: int, attempt_id: int
    ) -> ActionExecutionReconciliationRead:
        execution, attempt = self._load_owned_context(
            execution_id, attempt_id
        )
        reconciliation = self._repository.get_for_attempt(attempt.id)
        if reconciliation is None:
            self._raise(
                "action_execution_reconciliation_not_found",
                "Canonical action execution reconciliation not found",
            )
        if reconciliation.execution_id != execution.id:
            self._raise(
                "execution_reconciliation_mismatch",
                "Reconciliation does not belong to this execution",
            )
        if reconciliation.status != ActionExecutionReconciliationStatus.RUNNING:
            return ActionExecutionReconciliationRead.model_validate(reconciliation)

        claimed_at = self._as_utc(self._current_time())
        cutoff = claimed_at - timedelta(
            seconds=self._reconciliation_stale_after_seconds
        )
        is_stale = self._stored_as_utc(reconciliation.started_at) <= cutoff
        reason, proposal, strategy = self._recovery_block_reason(
            execution, attempt, reconciliation, is_stale
        )
        if reason is not None:
            self._raise_recovery_block(reason, execution.capability_name)
        runtime = self._repository.runtime_for(proposal)
        claim_status, reconciliation = self._repository.claim_recovery(
            execution,
            attempt,
            proposal,
            reconciliation,
            runtime,
            cutoff,
            claimed_at,
        )
        if claim_status == ReconciliationRecoveryClaimStatus.TERMINAL:
            return ActionExecutionReconciliationRead.model_validate(reconciliation)
        if claim_status == ReconciliationRecoveryClaimStatus.NOT_STALE:
            self._raise(
                "execution_reconciliation_not_stale",
                "Running reconciliation has recent durable progress",
            )
        return self._observe_and_finish(
            execution,
            attempt,
            proposal,
            reconciliation,
            runtime,
            strategy,
        )

    def _observe_and_finish(
        self,
        execution,
        attempt,
        proposal,
        reconciliation,
        runtime,
        strategy,
    ) -> ActionExecutionReconciliationRead:
        if self._tools is None:
            raise RuntimeError("Reconciliation observer registry is unavailable")
        try:
            observation = self._tools.execute(
                strategy.observer, {"application_id": proposal.target}
            )
        except Exception:
            return self._finish_inconclusive(
                execution, attempt, proposal, reconciliation, runtime
            )

        if not observation.success or observation.data is None:
            return self._finish_inconclusive(
                execution, attempt, proposal, reconciliation, runtime
            )

        raw_state = observation.data.get("status")
        if not isinstance(raw_state, str) or not raw_state.strip():
            return self._finish_inconclusive(
                execution, attempt, proposal, reconciliation, runtime
            )

        observed_state = raw_state.casefold()
        observed = {"state": observed_state}
        evidence = {
            "target": proposal.target,
            "observer": strategy.observer,
            "expected_state": strategy.expected_state,
            "observed_state": observed_state,
        }
        status = (
            ActionExecutionReconciliationStatus.DESIRED_STATE_OBSERVED
            if observed_state == strategy.expected_state
            else ActionExecutionReconciliationStatus.UNDESIRED_STATE_OBSERVED
        )
        reconciliation = self._repository.finish(
            execution,
            attempt,
            proposal,
            reconciliation,
            runtime,
            status,
            observed,
            evidence,
            None,
        )
        return ActionExecutionReconciliationRead.model_validate(reconciliation)

    def _finish_inconclusive(
        self, execution, attempt, proposal, reconciliation, runtime
    ) -> ActionExecutionReconciliationRead:
        reconciliation = self._repository.finish(
            execution,
            attempt,
            proposal,
            reconciliation,
            runtime,
            ActionExecutionReconciliationStatus.INCONCLUSIVE,
            None,
            None,
            self._observer_error(),
        )
        return ActionExecutionReconciliationRead.model_validate(reconciliation)

    def _load_canonical_context(
        self, execution_id: int, attempt_id: int
    ) -> tuple[ActionExecutionRecord, ActionExecutionAttemptRecord]:
        execution = self._repository.get_execution(execution_id)
        if execution is None:
            self._raise("action_execution_not_found", "Action execution not found")
        attempt = self._repository.get_attempt(attempt_id)
        if attempt is None:
            self._raise("execution_attempt_not_found", "Execution attempt not found")
        if attempt.execution_id != execution.id:
            self._raise(
                "execution_attempt_mismatch",
                "Execution attempt does not belong to this execution",
            )
        canonical = self._repository.get_canonical_attempt(execution.id)
        if (
            canonical is None
            or canonical.id != attempt.id
            or attempt.attempt_number != 1
        ):
            self._raise(
                "execution_attempt_not_canonical",
                "Only canonical execution attempt #1 can be reconciled",
            )
        return execution, attempt

    def _load_owned_context(
        self, execution_id: int, attempt_id: int
    ) -> tuple[ActionExecutionRecord, ActionExecutionAttemptRecord]:
        execution = self._repository.get_execution(execution_id)
        if execution is None:
            self._raise('action_execution_not_found', 'Action execution not found')
        attempt = self._repository.get_attempt(attempt_id)
        if attempt is None:
            self._raise('execution_attempt_not_found', 'Execution attempt not found')
        if attempt.execution_id != execution.id:
            self._raise(
                'execution_attempt_mismatch',
                'Execution attempt does not belong to this execution',
            )
        return execution, attempt

    def _load_proposal(self, execution: ActionExecutionRecord):
        proposal = self._repository.get_proposal(execution.proposal_id)
        if proposal is None:
            self._raise(
                "execution_reconciliation_context_invalid",
                "Action execution proposal is unavailable",
            )
        return proposal

    def _recovery_block_reason(
        self, execution, attempt, reconciliation, is_stale: bool
    ):
        canonical = self._repository.get_canonical_attempt(execution.id)
        proposal = self._repository.get_proposal(execution.proposal_id)
        strategy = None
        try:
            strategy = self._policy.strategy_for(execution.capability_name)
        except VerificationPolicyDeniedError:
            pass
        policy_matches = strategy is not None and (
            reconciliation.observer == strategy.observer
            and reconciliation.expected_outcome
            == {"state": strategy.expected_state}
        )
        reason = reconciliation_recovery_block_reason(
            reconciliation_running=(
                reconciliation.status
                == ActionExecutionReconciliationStatus.RUNNING
            ),
            is_stale=is_stale,
            canonical_attempt=(
                canonical is not None
                and canonical.id == attempt.id
                and attempt.attempt_number == 1
            ),
            invocation_started=attempt.invocation_started_at is not None,
            execution_outcome_unknown=(
                execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
            ),
            attempt_outcome_unknown=(
                attempt.status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
            ),
            outcome_certainty_unknown=(
                attempt.outcome_certainty == OutcomeCertainty.UNKNOWN
            ),
            proposal_available=proposal is not None,
            policy_available=strategy is not None,
            policy_matches=policy_matches,
        )
        return reason, proposal, strategy

    @classmethod
    def _validate_unknown_context(
        cls,
        execution: ActionExecutionRecord,
        attempt: ActionExecutionAttemptRecord,
    ) -> None:
        if attempt.invocation_started_at is None:
            cls._raise(
                "execution_attempt_invocation_not_started",
                "Execution attempt invocation was not started",
            )
        if execution.status != ActionExecutionStatus.OUTCOME_UNKNOWN:
            cls._raise(
                "execution_not_outcome_unknown",
                "Only an outcome-unknown execution can be reconciled",
            )
        if attempt.status != ActionExecutionAttemptStatus.OUTCOME_UNKNOWN:
            cls._raise(
                "execution_attempt_not_outcome_unknown",
                "Only an outcome-unknown execution attempt can be reconciled",
            )
        if attempt.outcome_certainty != OutcomeCertainty.UNKNOWN:
            cls._raise(
                "execution_attempt_certainty_not_unknown",
                "Execution attempt outcome certainty must remain unknown",
            )

    @staticmethod
    def _raise_recovery_block(
        reason: ReconciliationRecoveryBlockReason, capability_name: str
    ) -> None:
        if reason == ReconciliationRecoveryBlockReason.POLICY_UNAVAILABLE:
            raise VerificationPolicyDeniedError(
                f'No outcome verification policy for capability {capability_name!r}'
            )
        codes = {
            ReconciliationRecoveryBlockReason.RECONCILIATION_NOT_RUNNING:
                'execution_reconciliation_not_running',
            ReconciliationRecoveryBlockReason.NOT_STALE:
                'execution_reconciliation_not_stale',
            ReconciliationRecoveryBlockReason.ATTEMPT_NOT_CANONICAL:
                'execution_attempt_not_canonical',
            ReconciliationRecoveryBlockReason.INVOCATION_NOT_STARTED:
                'execution_attempt_invocation_not_started',
            ReconciliationRecoveryBlockReason.EXECUTION_NOT_OUTCOME_UNKNOWN:
                'execution_not_outcome_unknown',
            ReconciliationRecoveryBlockReason.ATTEMPT_NOT_OUTCOME_UNKNOWN:
                'execution_attempt_not_outcome_unknown',
            ReconciliationRecoveryBlockReason.OUTCOME_CERTAINTY_NOT_UNKNOWN:
                'execution_attempt_certainty_not_unknown',
            ReconciliationRecoveryBlockReason.PROPOSAL_UNAVAILABLE:
                'execution_reconciliation_context_invalid',
            ReconciliationRecoveryBlockReason.POLICY_MISMATCH:
                'execution_reconciliation_context_invalid',
        }
        raise ActionExecutionReconciliationError(
            codes[reason], 'Reconciliation recovery is not eligible'
        )

    @staticmethod
    def _observer_error() -> dict[str, str]:
        return {
            "code": "observer_failure",
            "message": "Unable to collect reliable reconciliation evidence",
        }

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("current_time must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stored_as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _raise(code: str, message: str) -> None:
        raise ActionExecutionReconciliationError(code, message)
