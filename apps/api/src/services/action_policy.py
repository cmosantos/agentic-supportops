from dataclasses import dataclass

from domain.action_proposal import ActionProposalCreate, ActionType


class InvalidActionProposalError(ValueError):
    pass


class InvalidActionTypeError(InvalidActionProposalError):
    pass


@dataclass(frozen=True)
class ActionDefinition:
    parameters: frozenset[str]


class AllowedActionRegistry:
    """Validates future simulated actions; it deliberately cannot execute them."""

    _definitions = {
        ActionType.RESTART_SIMULATED_SERVICE: ActionDefinition(
            parameters=frozenset({"service_name"})
        ),
        ActionType.UNLOCK_SIMULATED_USER: ActionDefinition(parameters=frozenset()),
        ActionType.RESET_SIMULATED_APPLICATION_STATE: ActionDefinition(
            parameters=frozenset()
        ),
    }

    def validate(self, proposal: ActionProposalCreate) -> ActionType:
        try:
            action_type = ActionType(proposal.action_type)
        except ValueError as error:
            raise InvalidActionTypeError(
                "Action type is not application-approved"
            ) from error
        definition = self._definitions[action_type]
        if set(proposal.parameters) != definition.parameters:
            raise InvalidActionProposalError(
                f"Expected parameters: {sorted(definition.parameters)}"
            )
        if not all(
            isinstance(value, str) and 0 < len(value) <= 100
            for value in proposal.parameters.values()
        ):
            raise InvalidActionProposalError(
                "Action parameters must be bounded non-empty strings"
            )
        return action_type

    @property
    def action_types(self) -> tuple[ActionType, ...]:
        return tuple(self._definitions)
