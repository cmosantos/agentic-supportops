import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import AIInvestigationRecord, IncidentRecord, InvestigationEventRecord
from domain.ai import (
    AIInvestigationResult,
    AIInvestigationStatus,
    InvestigationEventType,
    InvestigationRuntime,
    ProviderUsage,
)
from repositories.investigation_repository import (
    ActiveInvestigationExistsError,
    InvalidInvestigationTransitionError,
    InvestigationRepository,
)
from simulation.seed import seed_catalog


def result() -> AIInvestigationResult:
    return AIInvestigationResult(
        status=AIInvestigationStatus.COMPLETED,
        summary="Investigation completed.",
        diagnosis="DNS resolution failed.",
        confidence=0.9,
        supporting_evidence=["DNS lookup failed."],
        recommended_next_steps=["Review DNS service."],
        missing_information=[],
    )


@pytest.fixture
def run_database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    with sessions() as session:
        seed_catalog(session)
    yield engine, sessions
    engine.dispose()


def incident_id(session) -> int:
    return session.scalar(
        select(IncidentRecord.id).where(IncidentRecord.catalog_id == "INC-019")
    )


def test_running_uniqueness_is_database_backed_and_session_recovers(run_database) -> None:
    _, sessions = run_database
    with sessions() as first_session, sessions() as second_session:
        incident = incident_id(first_session)
        first = InvestigationRepository(first_session).start_ai_run(
            incident, "model-a"
        )
        with pytest.raises(ActiveInvestigationExistsError):
            InvestigationRepository(second_session).start_ai_run(incident, "model-b")
        assert second_session.scalar(select(AIInvestigationRecord.id)) == first.id


def test_completed_runs_are_immutable_history_and_latest_is_newest(run_database) -> None:
    _, sessions = run_database
    with sessions() as session:
        repository = InvestigationRepository(session)
        incident = incident_id(session)
        first = repository.start_ai_run(incident, "model-a")
        repository.complete_ai_run(first, result(), "response-a", ProviderUsage())
        second = repository.start_ai_run(incident, "model-b")
        repository.complete_ai_run(second, result(), "response-b", ProviderUsage())

        assert repository.get_ai_run(incident).id == second.id
        assert [item.id for item in repository.list_ai_runs(incident)] == [
            second.id,
            first.id,
        ]
        assert repository.get_ai_run_by_id(incident, first.id).response_id == "response-a"
        with pytest.raises(InvalidInvestigationTransitionError):
            repository.fail_ai_run(first, "late_failure", "must be rejected")


def test_terminal_event_and_state_roll_back_together_on_commit_failure(
    run_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, sessions = run_database
    with sessions() as session:
        repository = InvestigationRepository(session)
        incident = incident_id(session)
        run = repository.start_ai_run(incident, "model-a")
        run_id = run.id
        repository.record_event(
            run.id,
            InvestigationRuntime.MANUAL_RESPONSES,
            InvestigationEventType.RUN_COMPLETED,
            1,
            commit=False,
            status="completed",
        )

        def fail_commit() -> None:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            repository.complete_ai_run(run, result(), "response-a", ProviderUsage())

    with sessions() as verification:
        stored = verification.get(AIInvestigationRecord, run_id)
        assert stored.status == AIInvestigationStatus.RUNNING
        assert verification.scalars(
            select(InvestigationEventRecord).where(
                InvestigationEventRecord.investigation_id == run_id
            )
        ).all() == []
