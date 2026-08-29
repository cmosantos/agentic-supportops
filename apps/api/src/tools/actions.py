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
        application = self._repository.get_application(target)
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
        return success(
            "restart_simulated_service",
            application.id,
            {
                "target": application.id,
                "service_name": service.name,
                "previous_state": application.status,
                "current_state": "healthy",
                "restarted": True,
            },
        )
