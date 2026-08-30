from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from domain.action_execution import ActionExecutionStaleAssessmentRead
from repositories.action_execution_repository import (
    ActionExecutionRepository,
    StaleExecutionPersistenceResult,
    StaleExecutionPersistenceStatus,
)


class ActionExecutionRecoveryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ActionExecutionRecoveryService:
    def __init__(
        self,
        repository: ActionExecutionRepository,
        stale_after_seconds: int,
        current_time: Callable[[], datetime] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be greater than zero")
        self._repository = repository
        self._stale_after_seconds = stale_after_seconds
        self._current_time = current_time or self._utc_now

    def assess_stale_attempt(
        self, execution_id: int, attempt_id: int
    ) -> ActionExecutionStaleAssessmentRead:
        assessed_at = self._as_utc(self._current_time())
        cutoff = assessed_at - timedelta(seconds=self._stale_after_seconds)
        result = self._repository.classify_stale_interruption(
            execution_id, attempt_id, cutoff, assessed_at
        )
        return self._translate(result)

    @staticmethod
    def _translate(
        result: StaleExecutionPersistenceResult,
    ) -> ActionExecutionStaleAssessmentRead:
        if result.status in {
            StaleExecutionPersistenceStatus.TRANSITIONED,
            StaleExecutionPersistenceStatus.ALREADY_CLASSIFIED,
        }:
            if result.execution is None or result.attempt is None:
                raise RuntimeError("Recovery repository returned incomplete canonical state")
            return ActionExecutionStaleAssessmentRead.model_validate(
                {"execution": result.execution, "attempt": result.attempt}
            )

        errors = {
            StaleExecutionPersistenceStatus.NOT_ELIGIBLE: (
                "execution_attempt_not_stale",
                "Execution attempt has recent durable progress",
            ),
            StaleExecutionPersistenceStatus.EXECUTION_NOT_FOUND: (
                "action_execution_not_found",
                "Action execution not found",
            ),
            StaleExecutionPersistenceStatus.ATTEMPT_NOT_FOUND: (
                "execution_attempt_not_found",
                "Execution attempt not found",
            ),
            StaleExecutionPersistenceStatus.OWNERSHIP_MISMATCH: (
                "execution_attempt_mismatch",
                "Execution attempt does not belong to this execution",
            ),
            StaleExecutionPersistenceStatus.NONCANONICAL_ATTEMPT: (
                "execution_attempt_not_canonical",
                "Execution attempt is not the canonical attempt",
            ),
            StaleExecutionPersistenceStatus.TERMINAL_CONFLICT: (
                "execution_attempt_already_terminal",
                "Execution attempt was already classified by another cause",
            ),
            StaleExecutionPersistenceStatus.INCONSISTENT_STATE: (
                "execution_recovery_conflict",
                "Execution and attempt state are inconsistent",
            ),
            StaleExecutionPersistenceStatus.TRANSITION_CONFLICT: (
                "execution_recovery_conflict",
                "Execution recovery encountered a concurrent transition",
            ),
        }
        code, message = errors[result.status]
        raise ActionExecutionRecoveryError(code, message)

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("current_time must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)
