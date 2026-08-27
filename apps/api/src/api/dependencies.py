from collections.abc import Generator

from sqlalchemy.orm import Session

from db.session import SessionLocal


def get_db_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

