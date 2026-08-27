from collections.abc import Callable

from domain.investigation import ToolResult
from simulation.repository import SimulationRepository
from tools.endpoint import EndpointTools
from tools.identity import IdentityTools
from tools.monitoring import MonitoringTools
from tools.network import NetworkTools


ToolCallable = Callable[..., ToolResult]


class InvestigationToolRegistry:
    def __init__(self, repository: SimulationRepository | None = None) -> None:
        simulation = repository or SimulationRepository()
        identity = IdentityTools(simulation)
        endpoint = EndpointTools(simulation)
        network = NetworkTools(simulation)
        monitoring = MonitoringTools(simulation)
        self._tools: dict[str, ToolCallable] = {
            "get_user": identity.get_user,
            "get_account_status": identity.get_account_status,
            "get_user_groups": identity.get_user_groups,
            "get_user_licenses": identity.get_user_licenses,
            "get_mailbox": identity.get_mailbox,
            "get_mailbox_permissions": identity.get_mailbox_permissions,
            "get_device": endpoint.get_device,
            "get_cpu_usage": endpoint.get_cpu_usage,
            "get_memory_usage": endpoint.get_memory_usage,
            "get_disk_usage": endpoint.get_disk_usage,
            "get_network_config": endpoint.get_network_config,
            "get_service_status": endpoint.get_service_status,
            "check_gateway_connectivity": network.check_gateway_connectivity,
            "check_external_connectivity": network.check_external_connectivity,
            "check_dns_resolution": network.check_dns_resolution,
            "get_host_status": monitoring.get_host_status,
            "get_recent_alerts": monitoring.get_recent_alerts,
            "get_metrics": monitoring.get_metrics,
            "get_service_health": monitoring.get_service_health,
            "get_application_health": monitoring.get_application_health,
        }

    def execute(self, name: str, arguments: dict[str, str]) -> ToolResult:
        return self._tools[name](**arguments)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

