from dataclasses import dataclass


class VerificationPolicyDeniedError(PermissionError):
    pass


@dataclass(frozen=True)
class VerificationStrategy:
    observer: str
    observer_argument: str
    observed_field: str
    expected_state: str

    def arguments(self, target: str) -> dict[str, str]:
        return {self.observer_argument: target}

    def observed_state(self, data: dict) -> str | None:
        value = data.get(self.observed_field)
        if isinstance(value, str) and value.strip():
            return value.casefold()
        if isinstance(value, bool):
            return str(value).casefold()
        return None


class VerificationPolicy:
    """Server-controlled mapping from an executed mutation to a read-only check."""

    _strategies = {
        "restart_simulated_service": VerificationStrategy(
            observer="get_application_health",
            observer_argument="application_id",
            observed_field="status",
            expected_state="healthy",
        ),
        "unlock_simulated_user": VerificationStrategy(
            observer="get_account_status",
            observer_argument="user_id",
            observed_field="locked",
            expected_state="false",
        ),
        "reset_simulated_application_state": VerificationStrategy(
            observer="get_application_health",
            observer_argument="application_id",
            observed_field="status",
            expected_state="healthy",
        ),
    }

    def strategy_for(self, capability_name: str) -> VerificationStrategy:
        strategy = self._strategies.get(capability_name)
        if strategy is None:
            raise VerificationPolicyDeniedError(
                f"No outcome verification policy for capability '{capability_name}'"
            )
        return strategy
