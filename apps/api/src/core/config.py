import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./data/agentic_supportops.db"
    )


settings = Settings()

