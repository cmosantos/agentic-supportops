from simulation.repository import SimulationRepository
from tools.endpoint import EndpointTools
from tools.identity import IdentityTools
from tools.monitoring import MonitoringTools
from tools.network import NetworkTools


def test_identity_lookup_and_account_status() -> None:
    tools = IdentityTools(SimulationRepository())
    assert tools.get_user("Alice Johnson").data["id"] == "USR-ALICE"
    assert tools.get_account_status("USR-DAVID").data == {"enabled": False, "locked": False}
    missing = tools.get_user("USR-MISSING")
    assert missing.success is False
    assert missing.error.code == "user_not_found"


def test_mailbox_permissions_are_explicit() -> None:
    result = IdentityTools(SimulationRepository()).get_mailbox_permissions(
        "MBX-SUPPORT", "USR-BOB"
    )
    assert result.success is True
    assert result.data["full_access"] is True
    assert result.data["automapping"] is False
    missing = IdentityTools(SimulationRepository()).get_mailbox("MBX-MISSING")
    assert missing.success is False
    assert missing.error.code == "mailbox_not_found"


def test_endpoint_disk_metrics_and_missing_service() -> None:
    tools = EndpointTools(SimulationRepository())
    assert tools.get_disk_usage("WS-002").data["disk_percent"] == 98.4
    missing = tools.get_service_status("WS-001", "UnknownService")
    assert missing.success is False
    assert missing.error.code == "service_not_found"


def test_dns_behavior_is_deterministic() -> None:
    tools = NetworkTools(SimulationRepository())
    assert tools.check_external_connectivity("WS-003").data["external_reachable"] is True
    assert tools.check_dns_resolution("WS-003", "portal.contoso.example").data["resolved"] is False
    invalid = tools.check_dns_resolution("WS-003", " ")
    assert invalid.success is False
    assert invalid.error.code == "invalid_argument"
    missing = tools.check_external_connectivity("WS-999")
    assert missing.error.code == "resource_not_found"


def test_host_metrics_and_application_health() -> None:
    tools = MonitoringTools(SimulationRepository())
    assert tools.get_metrics("APP-02").data["cpu_percent"] == 97.6
    health = tools.get_application_health("SUPPORT-API")
    assert health.data["status"] == "degraded"
    assert health.data["latency_ms"] == 1850
    missing = tools.get_application_health("APP-MISSING")
    assert missing.success is False
    assert missing.error.code == "application_not_found"
