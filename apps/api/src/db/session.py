from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from db.schema import ensure_sqlite_schema_compatibility

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    from db import models  # noqa: F401
    from db.base import Base

    ensure_sqlite_schema_compatibility(engine)
    Base.metadata.create_all(bind=engine)
    from simulation.seed import seed_catalog

    with SessionLocal() as session:
        seed_catalog(session)
