from dataclasses import dataclass


class VerificationPolicyDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class VerificationStrategy:
    observer: str
    expected_state: str


class VerificationPolicy:
    """Server-controlled mapping from an executed mutation to a read-only check."""

    _strategies = {
        "restart_simulated_service": VerificationStrategy(
            observer="get_application_health", expected_state="healthy"
        )
    }

    def strategy_for(self, capability_name: str) -> VerificationStrategy:
        strategy = self._strategies.get(capability_name)
        if strategy is None:
            raise VerificationPolicyDeniedError(
                f"No outcome verification policy for capability '{capability_name}'"
            )
        return strategy
