from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure, success


class ActionTools:
    """Bounded lab actions. These methods never invoke host or operating-system APIs."""

    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def restart_simulated_service(
        self, target: str, service_name: str
    ) -> ToolResult:
        application = self._repository.get_application_for_action(target)
        if application is None:
            return failure(
                "restart_simulated_service",
                target,
                ToolErrorCode.APPLICATION_NOT_FOUND,
                "Application not found",
            )
        host = self._repository.get_host(application.host_id)
        service = next(
            (
                item
                for item in (host.services if host is not None else [])
                if item.name.casefold() == service_name.casefold()
            ),
            None,
        )
        if service is None:
            return failure(
                "restart_simulated_service",
                application.id,
                ToolErrorCode.SERVICE_NOT_FOUND,
                "Simulated application service not found",
            )
        current_state = self._repository.restart_application(application.id)
        return success(
            "restart_simulated_service",
            application.id,
            {
                "target": application.id,
                "service_name": service.name,
                "previous_state": application.status,
                "current_state": current_state,
                "restarted": True,
            },
        )

    def unlock_simulated_user(self, target: str) -> ToolResult:
        user = self._repository.get_user(target)
        if user is None:
            return failure(
                "unlock_simulated_user",
                target,
                ToolErrorCode.USER_NOT_FOUND,
                "User not found",
            )
        current_locked = self._repository.unlock_user(user.id)
        return success(
            "unlock_simulated_user",
            user.id,
            {
                "target": user.id,
                "previous_locked": user.account.locked,
                "current_locked": current_locked,
                "unlocked": not current_locked,
            },
        )

    def reset_simulated_application_state(self, target: str) -> ToolResult:
        application = self._repository.get_application_for_action(target)
        if application is None:
            return failure(
                "reset_simulated_application_state",
                target,
                ToolErrorCode.APPLICATION_NOT_FOUND,
                "Application not found",
            )
        current_state = self._repository.reset_application(application.id)
        return success(
            "reset_simulated_application_state",
            application.id,
            {
                "target": application.id,
                "previous_state": application.status,
                "current_state": current_state,
                "reset": True,
            },
        )
