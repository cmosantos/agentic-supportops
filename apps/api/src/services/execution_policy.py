from domain.action_proposal import ActionType


class ExecutionPolicyDeniedError(PermissionError):
    pass


class ExecutionPolicy:
    """A separate allowlist for post-approval mutation capabilities."""

    _allowed = frozenset(
        {
            ActionType.RESTART_SIMULATED_SERVICE.value,
            ActionType.UNLOCK_SIMULATED_USER.value,
            ActionType.RESET_SIMULATED_APPLICATION_STATE.value,
        }
    )

    def authorize(self, capability_name: str) -> None:
        if capability_name not in self._allowed:
            raise ExecutionPolicyDeniedError(
                f"Capability '{capability_name}' is not allowed for controlled execution"
            )
