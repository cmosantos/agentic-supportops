from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybookStep:
    tool: str
    arguments: dict[str, str]


PLAYBOOKS: dict[str, tuple[PlaybookStep, ...]] = {
    "INC-002": (
        PlaybookStep("get_account_status", {"user_id": "$user_id"}),
        PlaybookStep("get_mailbox", {"reference": "$mailbox_id"}),
        PlaybookStep(
            "get_mailbox_permissions",
            {"mailbox_id": "$mailbox_id", "user_id": "$user_id"},
        ),
    ),
    "INC-003": (
        PlaybookStep("get_account_status", {"user_id": "$user_id"}),
        PlaybookStep("get_mailbox", {"reference": "$mailbox_id"}),
        PlaybookStep(
            "get_mailbox_permissions",
            {"mailbox_id": "$mailbox_id", "user_id": "$user_id"},
        ),
    ),
    "INC-014": (
        PlaybookStep("get_device", {"device_id": "$device_id"}),
        PlaybookStep("get_disk_usage", {"device_id": "$device_id"}),
    ),
    "INC-019": (
        PlaybookStep("get_network_config", {"device_id": "$device_id"}),
        PlaybookStep("check_gateway_connectivity", {"device_id": "$device_id"}),
        PlaybookStep("check_external_connectivity", {"device_id": "$device_id"}),
        PlaybookStep(
            "check_dns_resolution", {"device_id": "$device_id", "hostname": "$hostname"}
        ),
    ),
    "INC-021": (
        PlaybookStep("get_host_status", {"host_id": "$host_id"}),
        PlaybookStep("get_metrics", {"host_id": "$host_id"}),
        PlaybookStep("get_recent_alerts", {"host_id": "$host_id"}),
    ),
    "INC-023": (
        PlaybookStep("get_application_health", {"application_id": "$application_id"}),
        PlaybookStep("get_host_status", {"host_id": "$host_id"}),
        PlaybookStep("get_metrics", {"host_id": "$host_id"}),
        PlaybookStep("get_recent_alerts", {"host_id": "$host_id"}),
    ),
    "INC-024": (
        PlaybookStep("get_application_health", {"application_id": "$application_id"}),
        PlaybookStep("get_host_status", {"host_id": "$host_id"}),
        PlaybookStep("get_metrics", {"host_id": "$host_id"}),
        PlaybookStep("get_recent_alerts", {"host_id": "$host_id"}),
    ),
    "INC-026": (
        PlaybookStep("get_user", {"reference": "$user_id"}),
        PlaybookStep("get_account_status", {"user_id": "$user_id"}),
    ),
}
