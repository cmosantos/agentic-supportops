from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure, success


class EndpointTools:
    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def get_device(self, device_id: str) -> ToolResult:
        device = self._repository.get_device(device_id)
        if device is None:
            return self._missing("get_device", device_id)
        return success("get_device", device.id, device.model_dump())

    def get_cpu_usage(self, device_id: str) -> ToolResult:
        return self._metric("get_cpu_usage", device_id, "cpu_percent")

    def get_memory_usage(self, device_id: str) -> ToolResult:
        return self._metric("get_memory_usage", device_id, "memory_percent")

    def get_disk_usage(self, device_id: str) -> ToolResult:
        return self._metric("get_disk_usage", device_id, "disk_percent")

    def get_network_config(self, device_id: str) -> ToolResult:
        device = self._repository.get_device(device_id)
        if device is None:
            return self._missing("get_network_config", device_id)
        return success("get_network_config", device.id, device.network.model_dump())

    def get_service_status(self, resource_id: str, service_name: str) -> ToolResult:
        resource = self._repository.get_device(resource_id) or self._repository.get_host(resource_id)
        if resource is None:
            return self._missing("get_service_status", resource_id)
        service = next(
            (item for item in resource.services if item.name.casefold() == service_name.casefold()),
            None,
        )
        if service is None:
            return failure(
                "get_service_status",
                resource_id,
                ToolErrorCode.SERVICE_NOT_FOUND,
                f"Service '{service_name}' not found",
            )
        return success("get_service_status", resource_id, service.model_dump())

    def _metric(self, tool: str, device_id: str, field: str) -> ToolResult:
        device = self._repository.get_device(device_id)
        if device is None:
            return self._missing(tool, device_id)
        value = getattr(device, field)
        return success(tool, device.id, {field: value})

    @staticmethod
    def _missing(tool: str, resource: str) -> ToolResult:
        return failure(tool, resource, ToolErrorCode.RESOURCE_NOT_FOUND, "Resource not found")

