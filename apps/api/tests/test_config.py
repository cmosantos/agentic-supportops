from pathlib import Path

import pytest

from core.config import Settings, load_local_environment


def test_process_environment_takes_precedence_over_local_env(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("OPENAI_MODEL=local-model\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_MODEL", "process-model")

    load_local_environment(env_file)

    assert __import__("os").environ["OPENAI_MODEL"] == "process-model"


def test_openai_execution_control_defaults(monkeypatch) -> None:
    for name in (
        "OPENAI_MAX_RETRIES",
        "OPENAI_TIMEOUT_SECONDS",
        "AI_MAX_OUTPUT_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.openai_max_retries == 0
    assert settings.openai_timeout_seconds == 60
    assert settings.ai_max_output_tokens == 2000


def test_process_environment_overrides_execution_controls(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "1")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "45.5")
    monkeypatch.setenv("AI_MAX_OUTPUT_TOKENS", "1500")

    settings = Settings()

    assert settings.openai_max_retries == 1
    assert settings.openai_timeout_seconds == 45.5
    assert settings.ai_max_output_tokens == 1500


def test_opentelemetry_defaults_to_disabled_and_no_exporter(monkeypatch) -> None:
    for name in ("OTEL_ENABLED", "OTEL_SERVICE_NAME", "OTEL_EXPORTER"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.otel_enabled is False
    assert settings.otel_service_name == "agentic-supportops"
    assert settings.otel_exporter == "none"


def test_opentelemetry_configuration_remains_process_overridable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OTEL_ENABLED", "true")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "supportops-local")
    monkeypatch.setenv("OTEL_EXPORTER", "console")

    settings = Settings()

    assert settings.otel_enabled is True
    assert settings.otel_service_name == "supportops-local"
    assert settings.otel_exporter == "console"


def test_tool_transport_defaults_direct_and_supports_bounded_mcp(monkeypatch) -> None:
    monkeypatch.delenv("TOOL_TRANSPORT", raising=False)
    monkeypatch.delenv("MCP_TIMEOUT_SECONDS", raising=False)
    assert Settings().tool_transport == "direct"
    assert Settings().mcp_timeout_seconds == 10

    monkeypatch.setenv("TOOL_TRANSPORT", "mcp")
    monkeypatch.setenv("MCP_TIMEOUT_SECONDS", "4.5")
    configured = Settings()
    assert configured.tool_transport == "mcp"
    assert configured.mcp_timeout_seconds == 4.5


def test_stale_execution_assessment_threshold_defaults_to_five_minutes(
    monkeypatch,
) -> None:
    monkeypatch.delenv(
        "ACTION_EXECUTION_ATTEMPT_STALE_AFTER_SECONDS", raising=False
    )

    assert Settings().action_execution_attempt_stale_after_seconds == 300


def test_stale_execution_assessment_threshold_is_process_overridable(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ACTION_EXECUTION_ATTEMPT_STALE_AFTER_SECONDS", "900")

    assert Settings().action_execution_attempt_stale_after_seconds == 900


@pytest.mark.parametrize("invalid_threshold", ["0", "-1"])
def test_stale_execution_assessment_threshold_must_be_positive(
    monkeypatch, invalid_threshold: str
) -> None:
    monkeypatch.setenv(
        "ACTION_EXECUTION_ATTEMPT_STALE_AFTER_SECONDS", invalid_threshold
    )

    with pytest.raises(
        ValueError,
        match="ACTION_EXECUTION_ATTEMPT_STALE_AFTER_SECONDS must be greater than zero",
    ):
        Settings()
