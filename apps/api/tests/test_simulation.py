from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import IncidentRecord
from simulation.repository import SimulationRepository
from simulation.seed import reset_simulation, seed_catalog


def test_fixture_contains_expected_healthy_and_unhealthy_resources() -> None:
    repository = SimulationRepository()
    assert repository.load_fixture().environment.organization == "Contoso"
    assert repository.get_device("WS-001").disk_percent == 42
    assert repository.get_device("WS-002").disk_percent == 98.4
    assert repository.get_device("WS-003").network.dns_operational is False
    assert repository.get_host("APP-01").status == "healthy"
    assert repository.get_host("APP-02").cpu_percent == 97.6
    assert repository.get_application("SUPPORT-API").status == "degraded"


def test_seed_is_deterministic_and_idempotent(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'seed.db'}")
    Base.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        assert seed_catalog(session) == 26
        assert seed_catalog(session) == 0
        incidents = list(session.scalars(select(IncidentRecord).order_by(IncidentRecord.catalog_id)))
        assert len(incidents) == 26
        assert incidents[0].catalog_id == "INC-001"
        assert incidents[-1].catalog_id == "INC-026"
    engine.dispose()


def test_reset_restores_known_baseline(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reset.db'}")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    assert reset_simulation(engine, sessions) == 26
    with sessions() as session:
        session.add(
            IncidentRecord(
                title="Temporary",
                description="Will be removed",
                category="test",
                priority="low",
                status="open",
                requester="test@contoso.example",
                investigation_context={},
            )
        )
        session.commit()
    assert reset_simulation(engine, sessions) == 26
    with sessions() as session:
        assert len(list(session.scalars(select(IncidentRecord)))) == 26
    engine.dispose()
