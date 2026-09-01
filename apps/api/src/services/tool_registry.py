import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from domain.investigation import ToolErrorCode, ToolResult
from simulation.repository import SimulationRepository
from tools.common import failure
from tools.actions import ActionTools
from tools.endpoint import EndpointTools
from tools.identity import IdentityTools
from tools.monitoring import MonitoringTools
from tools.network import NetworkTools

ToolCallable = Callable[..., ToolResult]


@dataclass(frozen=True)
class ToolDefinition:
    callable: ToolCallable
    description: str
    parameters: tuple[tuple[str, str], ...]

    def openai_schema(self, name: str) -> dict[str, Any]:
        properties = {
            parameter: {"type": "string", "description": description}
            for parameter, description in self.parameters
        }
        return {
            "type": "function",
            "name": name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
            "strict": True,
        }


class InvestigationToolRegistry:
    transport = "direct"
    _execution_only = frozenset(
        {
            "restart_simulated_service",
            "unlock_simulated_user",
            "reset_simulated_application_state",
        }
    )

    def __init__(self, repository: SimulationRepository | None = None) -> None:
        simulation = repository or SimulationRepository()
        identity = IdentityTools(simulation)
        endpoint = EndpointTools(simulation)
        network = NetworkTools(simulation)
        monitoring = MonitoringTools(simulation)
        actions = ActionTools(simulation)
        self._tools = self._build_definitions(identity, endpoint, network, monitoring, actions)

    @staticmethod
    def _build_definitions(identity, endpoint, network, monitoring, actions) -> dict[str, ToolDefinition]:
        return {
            "get_user": ToolDefinition(identity.get_user, "Read a simulated user's factual profile. Read-only.", (("reference", "User ID, display name, or email address."),)),
            "get_account_status": ToolDefinition(identity.get_account_status, "Read whether a simulated user account is enabled or locked. Read-only.", (("user_id", "User identifier."),)),
            "get_user_groups": ToolDefinition(identity.get_user_groups, "Read a simulated user's group memberships. Read-only.", (("user_id", "User identifier."),)),
            "get_user_licenses": ToolDefinition(identity.get_user_licenses, "Read licenses assigned to a simulated user. Read-only.", (("user_id", "User identifier."),)),
            "get_mailbox": ToolDefinition(identity.get_mailbox, "Read factual simulated mailbox properties and quota usage. Read-only.", (("reference", "Mailbox ID or email address."),)),
            "get_mailbox_permissions": ToolDefinition(identity.get_mailbox_permissions, "Read factual FullAccess, SendAs, automapping, and deny permissions for one user on a simulated mailbox. Does not modify permissions.", (("mailbox_id", "Mailbox identifier."), ("user_id", "User identifier."))),
            "get_device": ToolDefinition(endpoint.get_device, "Read the complete factual state of a simulated workstation. Read-only.", (("device_id", "Workstation identifier."),)),
            "get_cpu_usage": ToolDefinition(endpoint.get_cpu_usage, "Read simulated workstation CPU utilization. Read-only.", (("device_id", "Workstation identifier."),)),
            "get_memory_usage": ToolDefinition(endpoint.get_memory_usage, "Read simulated workstation memory utilization. Read-only.", (("device_id", "Workstation identifier."),)),
            "get_disk_usage": ToolDefinition(endpoint.get_disk_usage, "Read simulated workstation disk utilization. Read-only.", (("device_id", "Workstation identifier."),)),
            "get_network_config": ToolDefinition(endpoint.get_network_config, "Read a simulated workstation's IP, gateway, DNS, and connectivity configuration. Read-only.", (("device_id", "Workstation identifier."),)),
            "get_service_status": ToolDefinition(endpoint.get_service_status, "Read one simulated endpoint or host service status. Does not restart or modify it.", (("resource_id", "Device or host identifier."), ("service_name", "Exact service name."))),
            "check_gateway_connectivity": ToolDefinition(network.check_gateway_connectivity, "Observe simulated gateway connectivity from a device. No real network request is made.", (("device_id", "Workstation identifier."),)),
            "check_external_connectivity": ToolDefinition(network.check_external_connectivity, "Observe simulated external IP connectivity from a device. No real network request is made.", (("device_id", "Workstation identifier."),)),
            "check_dns_resolution": ToolDefinition(network.check_dns_resolution, "Test simulated DNS resolution from a device to a hostname. No real network request is made.", (("device_id", "Workstation identifier."), ("hostname", "Hostname to resolve."))),
            "get_host_status": ToolDefinition(monitoring.get_host_status, "Read the factual health status of a simulated infrastructure host. Read-only.", (("host_id", "Host identifier."),)),
            "get_recent_alerts": ToolDefinition(monitoring.get_recent_alerts, "Read current simulated monitoring alerts for a host. Read-only.", (("host_id", "Host identifier."),)),
            "get_metrics": ToolDefinition(monitoring.get_metrics, "Read simulated host CPU, memory, and disk metrics. Read-only.", (("host_id", "Host identifier."),)),
            "get_service_health": ToolDefinition(monitoring.get_service_health, "Read one simulated host service's health. Does not restart or modify it.", (("resource_id", "Host identifier."), ("service_name", "Exact service name."))),
            "get_application_health": ToolDefinition(monitoring.get_application_health, "Read simulated application status, latency, error rate, and connection-pool utilization. Read-only.", (("application_id", "Application identifier."),)),
            "restart_simulated_service": ToolDefinition(actions.restart_simulated_service, "Restart exactly one service inside the deterministic lab application abstraction. No host process is touched.", (("target", "Simulated application identifier."), ("service_name", "Exact simulated service name."))),
            "unlock_simulated_user": ToolDefinition(actions.unlock_simulated_user, "Unlock exactly one user inside the deterministic Contoso simulation. No real account is touched.", (("target", "Simulated user identifier."),)),
            "reset_simulated_application_state": ToolDefinition(actions.reset_simulated_application_state, "Reset exactly one application inside the deterministic Contoso simulation. No host process is touched.", (("target", "Simulated application identifier."),)),
        }

    def execute(self, name: str, arguments: dict[str, str]) -> ToolResult:
        return self._tools[name].callable(**arguments)

    def dispatch(self, name: str, raw_arguments: str) -> tuple[dict[str, Any], ToolResult]:
        arguments, error = self.validate_arguments(name, raw_arguments)
        if error is not None:
            return arguments, error
        return arguments, self.execute(name, arguments)

    def validate_arguments(
        self, name: str, raw_arguments: str
    ) -> tuple[dict[str, Any], ToolResult | None]:
        definition = self._tools.get(name)
        if definition is None or name in self._execution_only:
            return {}, failure(name, "unknown", ToolErrorCode.UNKNOWN_TOOL, f"Unknown tool '{name}'")
        try:
            arguments = json.loads(raw_arguments)
        except (json.JSONDecodeError, TypeError):
            return {}, failure(name, "unknown", ToolErrorCode.MALFORMED_ARGUMENTS, "Tool arguments must be valid JSON")
        if not isinstance(arguments, dict):
            return {}, failure(name, "unknown", ToolErrorCode.MALFORMED_ARGUMENTS, "Tool arguments must be a JSON object")
        expected = set(inspect.signature(definition.callable).parameters)
        if set(arguments) != expected or not all(isinstance(value, str) for value in arguments.values()):
            return arguments, failure(name, self._resource(arguments), ToolErrorCode.MALFORMED_ARGUMENTS, f"Expected string arguments: {sorted(expected)}")
        return arguments, None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name in self._tools if name not in self._execution_only)

    @property
    def openai_tools(self) -> list[dict[str, Any]]:
        return [self._tools[name].openai_schema(name) for name in self.names]

    def openai_tools_for(self, names: tuple[str, ...]) -> list[dict[str, Any]]:
        return [self._tools[name].openai_schema(name) for name in names]

    def description(self, name: str) -> str:
        return self._tools[name].description

    @staticmethod
    def _resource(arguments: dict[str, Any]) -> str:
        for key in ("resource_id", "device_id", "host_id", "application_id", "mailbox_id", "user_id", "reference"):
            if key in arguments:
                return str(arguments[key])[:100]
        return "unknown"
