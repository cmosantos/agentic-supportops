import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def load_local_environment(path: Path = REPOSITORY_ROOT / ".env.local") -> None:
    load_dotenv(path, override=False)


load_local_environment()


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL", "sqlite:///./data/agentic_supportops.db"
        )
    )
    openai_api_key: str | None = field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY") or None,
        repr=False,
    )
    openai_model: str = field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    )
    openai_max_retries: int = field(
        default_factory=lambda: int(os.getenv("OPENAI_MAX_RETRIES", "0"))
    )
    openai_timeout_seconds: float = field(
        default_factory=lambda: float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60"))
    )
    ai_max_response_iterations: int = field(
        default_factory=lambda: int(os.getenv("AI_MAX_RESPONSE_ITERATIONS", "8"))
    )
    ai_max_tool_calls: int = field(
        default_factory=lambda: int(os.getenv("AI_MAX_TOOL_CALLS", "16"))
    )
    ai_max_identical_tool_calls: int = field(
        default_factory=lambda: int(os.getenv("AI_MAX_IDENTICAL_TOOL_CALLS", "2"))
    )
    ai_max_output_tokens: int = field(
        default_factory=lambda: int(os.getenv("AI_MAX_OUTPUT_TOKENS", "2000"))
    )
    otel_enabled: bool = field(
        default_factory=lambda: os.getenv("OTEL_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"}
    )
    otel_service_name: str = field(
        default_factory=lambda: os.getenv(
            "OTEL_SERVICE_NAME", "agentic-supportops"
        )
    )
    otel_exporter: str = field(
        default_factory=lambda: os.getenv("OTEL_EXPORTER", "none").lower()
    )


settings = Settings()
