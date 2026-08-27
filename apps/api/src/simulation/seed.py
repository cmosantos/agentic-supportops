import argparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from db.base import Base
from db.models import IncidentRecord
from db.session import SessionLocal, engine
from domain.incident import IncidentPriority, IncidentStatus
from simulation.repository import SimulationRepository


def seed_catalog(session: Session, repository: SimulationRepository | None = None) -> int:
    fixture = (repository or SimulationRepository()).load_fixture()
    existing = set(session.scalars(select(IncidentRecord.catalog_id)).all())
    added = 0
    for item in fixture.incidents:
        if item.catalog_id in existing:
            continue
        session.add(
            IncidentRecord(
                **item.model_dump(exclude={"priority"}),
                priority=IncidentPriority(item.priority),
                status=IncidentStatus.OPEN,
            )
        )
        added += 1
    session.commit()
    return added


def reset_simulation(
    database_engine=engine,
    session_factory: sessionmaker[Session] = SessionLocal,
    repository: SimulationRepository | None = None,
) -> int:
    Base.metadata.drop_all(bind=database_engine)
    Base.metadata.create_all(bind=database_engine)
    with session_factory() as session:
        return seed_catalog(session, repository)


def catalog_count(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(IncidentRecord).where(IncidentRecord.catalog_id.is_not(None))
    ) or 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset the deterministic Contoso simulation")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop local application tables and restore the known fixture baseline",
    )
    args = parser.parse_args()
    if not args.reset:
        parser.error("--reset is required because this operation deletes local database data")
    count = reset_simulation()
    print(f"Simulation reset complete: {count} catalog incidents seeded")


if __name__ == "__main__":
    main()
