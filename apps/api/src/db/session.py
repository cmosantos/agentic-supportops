from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    from db import models  # noqa: F401
    from db.base import Base

    Base.metadata.create_all(bind=engine)

