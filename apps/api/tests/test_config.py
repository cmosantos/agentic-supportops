from pathlib import Path

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
