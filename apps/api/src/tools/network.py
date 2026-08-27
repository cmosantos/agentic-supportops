from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure, success


class NetworkTools:
    def __init__(self, repository: SimulationRepository) -> None:
        self._repository = repository

    def check_gateway_connectivity(self, device_id: str) -> ToolResult:
        return self._connectivity("check_gateway_connectivity", device_id, "gateway_reachable")

    def check_external_connectivity(self, device_id: str) -> ToolResult:
        return self._connectivity("check_external_connectivity", device_id, "external_reachable")

    def check_dns_resolution(self, device_id: str, hostname: str) -> ToolResult:
        if not hostname.strip():
            return failure(
                "check_dns_resolution",
                device_id,
                ToolErrorCode.INVALID_ARGUMENT,
                "Hostname must not be empty",
            )
        device = self._repository.get_device(device_id)
        if device is None:
            return self._missing("check_dns_resolution", device_id)
        return success(
            "check_dns_resolution",
            device.id,
            {"hostname": hostname, "resolved": device.network.dns_operational},
        )

    def _connectivity(self, tool: str, device_id: str, field: str) -> ToolResult:
        device = self._repository.get_device(device_id)
        if device is None:
            return self._missing(tool, device_id)
        return success(tool, device.id, {field: getattr(device.network, field)})

    @staticmethod
    def _missing(tool: str, resource: str) -> ToolResult:
        return failure(tool, resource, ToolErrorCode.RESOURCE_NOT_FOUND, "Resource not found")

