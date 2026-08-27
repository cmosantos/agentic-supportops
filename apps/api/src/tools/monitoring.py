from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure, success


class MonitoringTools:
    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def get_host_status(self, host_id: str) -> ToolResult:
        host = self._repository.get_host(host_id)
        if host is None:
            return self._missing("get_host_status", host_id)
        return success(
            "get_host_status", host.id, {"status": host.status, "host_type": host.host_type}
        )

    def get_recent_alerts(self, host_id: str) -> ToolResult:
        if self._repository.get_host(host_id) is None:
            return self._missing("get_recent_alerts", host_id)
        alerts = [
            alert.model_dump()
            for alert in self._repository.load_fixture().environment.alerts
            if alert.host_id == host_id.upper()
        ]
        return success("get_recent_alerts", host_id.upper(), {"alerts": alerts})

    def get_metrics(self, host_id: str) -> ToolResult:
        host = self._repository.get_host(host_id)
        if host is None:
            return self._missing("get_metrics", host_id)
        return success(
            "get_metrics",
            host.id,
            {
                "cpu_percent": host.cpu_percent,
                "memory_percent": host.memory_percent,
                "disk_percent": host.disk_percent,
            },
        )

    def get_service_health(self, resource_id: str, service_name: str) -> ToolResult:
        host = self._repository.get_host(resource_id)
        if host is None:
            return self._missing("get_service_health", resource_id)
        service = next(
            (item for item in host.services if item.name.casefold() == service_name.casefold()),
            None,
        )
        if service is None:
            return failure(
                "get_service_health",
                resource_id,
                ToolErrorCode.SERVICE_NOT_FOUND,
                f"Service '{service_name}' not found",
            )
        return success("get_service_health", host.id, service.model_dump())

    def get_application_health(self, application_id: str) -> ToolResult:
        application = self._repository.get_application(application_id)
        if application is None:
            return failure(
                "get_application_health",
                application_id,
                ToolErrorCode.APPLICATION_NOT_FOUND,
                "Application not found",
            )
        return success("get_application_health", application.id, application.model_dump())

    @staticmethod
    def _missing(tool: str, resource: str) -> ToolResult:
        return failure(tool, resource, ToolErrorCode.RESOURCE_NOT_FOUND, "Resource not found")
