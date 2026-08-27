import json

from services.tool_registry import InvestigationToolRegistry


def test_openai_tool_schemas_are_strict_read_only_and_complete() -> None:
    registry = InvestigationToolRegistry()
    schemas = {schema["name"]: schema for schema in registry.openai_tools}
    assert set(schemas) == set(registry.names)
    assert schemas["get_mailbox_permissions"]["parameters"]["required"] == [
        "mailbox_id",
        "user_id",
    ]
    assert schemas["check_dns_resolution"]["strict"] is True
    assert schemas["check_dns_resolution"]["parameters"]["additionalProperties"] is False
    assert all(
        "read" in schema["description"].lower()
        or "observe" in schema["description"].lower()
        or "test" in schema["description"].lower()
        for schema in schemas.values()
    )


def test_dispatcher_handles_valid_unknown_malformed_and_resource_errors() -> None:
    registry = InvestigationToolRegistry()
    arguments, valid = registry.dispatch("get_disk_usage", '{"device_id":"WS-002"}')
    assert arguments == {"device_id": "WS-002"}
    assert valid.data["disk_percent"] == 98.4
    _, unknown = registry.dispatch("delete_device", "{}")
    assert unknown.error.code == "unknown_tool"
    _, malformed = registry.dispatch("get_disk_usage", "not-json")
    assert malformed.error.code == "malformed_arguments"
    _, wrong_shape = registry.dispatch("get_disk_usage", '{"device_id":42}')
    assert wrong_shape.error.code == "malformed_arguments"
    _, missing = registry.dispatch("get_disk_usage", json.dumps({"device_id": "WS-999"}))
    assert missing.error.code == "resource_not_found"

