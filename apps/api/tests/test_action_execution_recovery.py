from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from inspect import signature
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.base import Base
from db.models import (
    AIInvestigationRecord,
    ActionExecutionAttemptRecord,
    ActionExecutionRecord,
    ActionProposalRecord,
    IncidentRecord,
    InvestigationEventRecord,
)
from domain.action_execution import (
    ActionExecutionAttemptStatus,
    ActionExecutionCompletionBasis,
    ActionExecutionStatus,
    FailureCause,
    OutcomeCertainty,
)
from domain.action_proposal import ActionRisk, ActionType, ApprovalStatus
from domain.ai import AIInvestigationStatus, InvestigationEventType
from domain.incident import IncidentPriority, IncidentStatus
from integrations.responses_gateway import ResponsesGateway
from repositories.action_execution_attempt_repository import (
    ActionExecutionAttemptRepository,
)
from repositories.action_execution_repository import (
    ActionExecutionRepository,
    StaleExecutionPersistenceStatus,
)
from repositories.investigation_repository import InvestigationRepository
from services.tool_registry import InvestigationToolRegistry
from services.action_execution_recovery_service import (
    ActionExecutionRecoveryError,
    ActionExecutionRecoveryService,
)
from tools.actions import ActionTools


NOW = datetime(2026, 8, 30, 15, tzinfo=timezone.utc)
CUTOFF = NOW - timedelta(seconds=300)
OLD = NOW - timedelta(seconds=600)
RECENT = NOW - timedelta(seconds=60)


@pytest.fixture
def recovery_sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'execution-recovery.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    yield sessions
    Base.metadata.drop_all(engine)
    engine.dispose()


def seed_recovery_context(
    sessions,
    *,
    claimed_at: datetime = OLD,
    invocation_started_at: datetime | None = None,
    execution_status: ActionExecutionStatus = ActionExecutionStatus.RUNNING,
    attempt_status: ActionExecutionAttemptStatus = ActionExecutionAttemptStatus.RUNNING,
    failure_cause: FailureCause | None = None,
    outcome_certainty: OutcomeCertainty | None = None,
    result: dict | None = None,
) -> tuple[int, int, int]:
    with sessions() as session:
        incident = IncidentRecord(
            title="Recovery test incident",
            description="Deterministic stale execution persistence test",
            category="application",
            priority=IncidentPriority.HIGH,
            status=IncidentStatus.INVESTIGATING,
            requester="operator@example.com",
            catalog_id=None,
            investigation_context={},
        )
        session.add(incident)
        session.flush()
        investigation = AIInvestigationRecord(
            incident_id=incident.id,
            mode="ai",
            status=AIInvestigationStatus.COMPLETED,
            model="deterministic-test",
            result={},
            usage={},
        )
        session.add(investigation)
        session.flush()
        proposal = ActionProposalRecord(
            investigation_id=investigation.id,
            incident_id=incident.id,
            action_type=ActionType.RESTART_SIMULATED_SERVICE,
            target="SUPPORT-API",
            parameters={"service_name": "SupportApi"},
            rationale="Approved deterministic recovery fixture",
            supporting_evidence_ids=[],
            risk_level=ActionRisk.MEDIUM,
            approval_status=ApprovalStatus.APPROVED,
            decision_at=OLD,
        )
        session.add(proposal)
        session.flush()
        execution = ActionExecutionRecord(
            proposal_id=proposal.id,
            incident_id=incident.id,
            capability_name=ActionType.RESTART_SIMULATED_SERVICE.value,
            status=execution_status,
            started_at=claimed_at,
            completed_at=(NOW if execution_status == ActionExecutionStatus.COMPLETED else None),
            result=result,
            error=None,
            completion_basis=(
                ActionExecutionCompletionBasis.ACKNOWLEDGED_RESULT
                if execution_status == ActionExecutionStatus.COMPLETED
                else None
            ),
        )
        session.add(execution)
        session.flush()
        attempt = ActionExecutionAttemptRecord(
            execution_id=execution.id,
            attempt_number=1,
            status=attempt_status,
            claimed_at=claimed_at,
            invocation_started_at=invocation_started_at,
            completed_at=(NOW if attempt_status != ActionExecutionAttemptStatus.RUNNING else None),
            result=result,
            error=None,
            failure_cause=failure_cause,
            outcome_certainty=outcome_certainty,
        )
        session.add(attempt)
        session.commit()
        return execution.id, attempt.id, investigation.id


def classify(sessions, execution_id: int, attempt_id: int):
    with sessions() as session:
        return ActionExecutionRepository(session).classify_stale_interruption(
            execution_id, attempt_id, CUTOFF, NOW
        )


def assess(
    sessions,
    execution_id: int,
    attempt_id: int,
    *,
    now: datetime = NOW,
    stale_after_seconds: int = 300,
):
    with sessions() as session:
        return ActionExecutionRecoveryService(
            ActionExecutionRepository(session),
            stale_after_seconds,
            current_time=lambda: now,
        ).assess_stale_attempt(execution_id, attempt_id)


def assert_recovery_error(code: str, operation) -> None:
    with pytest.raises(ActionExecutionRecoveryError) as captured:
        operation()
    assert captured.value.code == code


def persisted_state(sessions, execution_id: int, attempt_id: int):
    with sessions() as session:
        execution = session.get(ActionExecutionRecord, execution_id)
        attempt = session.get(ActionExecutionAttemptRecord, attempt_id)
        events = list(
            session.scalars(
                select(InvestigationEventRecord).where(
                    InvestigationEventRecord.event_type
                    == InvestigationEventType.EXECUTION_ATTEMPT_INTERRUPTION_ASSESSED
                )
            )
        )
        attempts = list(
            session.scalars(
                select(ActionExecutionAttemptRecord).where(
                    ActionExecutionAttemptRecord.execution_id == execution_id
                )
            )
        )
        return execution, attempt, events, attempts


def test_stale_before_invocation_is_failed_not_applied(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.TRANSITIONED
    assert execution.status == ActionExecutionStatus.FAILED
    assert execution.completed_at is not None
    assert attempt.status == ActionExecutionAttemptStatus.FAILED
    assert attempt.failure_cause == FailureCause.PROCESS_INTERRUPTED
    assert attempt.outcome_certainty == OutcomeCertainty.NOT_APPLIED
    assert attempt.error["code"] == "execution_interrupted_before_invocation"
    assert len(events) == 1
    metadata = events[0].event_metadata
    assert metadata["incident_id"] == execution.incident_id
    assert metadata["proposal_id"] == execution.proposal_id
    assert metadata["execution_id"] == execution.id
    assert metadata["attempt_id"] == attempt.id
    assert metadata["capability_name"] == "restart_simulated_service"
    assert metadata["failure_cause"] == "process_interrupted"
    assert metadata["assessment_reason"] == "stale_before_invocation"
    assert metadata["outcome_certainty"] == "not_applied"


def test_stale_after_invocation_is_outcome_unknown(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions, invocation_started_at=OLD
    )

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.TRANSITIONED
    assert execution.status == ActionExecutionStatus.OUTCOME_UNKNOWN
    assert execution.completed_at is None
    assert attempt.status == ActionExecutionAttemptStatus.OUTCOME_UNKNOWN
    assert attempt.failure_cause == FailureCause.PROCESS_INTERRUPTED
    assert attempt.outcome_certainty == OutcomeCertainty.UNKNOWN
    assert attempt.error["code"] == "execution_interrupted_after_invocation"
    assert len(events) == 1
    assert events[0].event_metadata["assessment_reason"] == "stale_after_invocation"
    assert events[0].event_metadata["outcome_certainty"] == "unknown"


@pytest.mark.parametrize(
    ("claimed_at", "invocation_started_at"),
    [(OLD, RECENT), (RECENT, None)],
)
def test_recent_progress_is_not_eligible(
    recovery_sessions, claimed_at, invocation_started_at
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        claimed_at=claimed_at,
        invocation_started_at=invocation_started_at,
    )

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.NOT_ELIGIBLE
    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


@pytest.mark.parametrize("invocation_started_at", [None, OLD])
def test_duplicate_classification_is_idempotent(
    recovery_sessions, invocation_started_at
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions, invocation_started_at=invocation_started_at
    )

    first = classify(recovery_sessions, execution_id, attempt_id)
    second = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, attempts = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert first.status == StaleExecutionPersistenceStatus.TRANSITIONED
    assert second.status == StaleExecutionPersistenceStatus.ALREADY_CLASSIFIED
    expected_status = (
        ActionExecutionStatus.OUTCOME_UNKNOWN
        if invocation_started_at is not None
        else ActionExecutionStatus.FAILED
    )
    assert execution.status == expected_status
    assert attempt.failure_cause == FailureCause.PROCESS_INTERRUPTED
    assert len(events) == 1
    assert len(attempts) == 1


@pytest.mark.parametrize(
    ("execution_status", "attempt_status", "failure_cause", "certainty", "result"),
    [
        (
            ActionExecutionStatus.COMPLETED,
            ActionExecutionAttemptStatus.COMPLETED,
            None,
            OutcomeCertainty.APPLIED_ACKNOWLEDGED,
            {"success": True},
        ),
        (
            ActionExecutionStatus.FAILED,
            ActionExecutionAttemptStatus.FAILED,
            FailureCause.TOOL_REJECTED,
            OutcomeCertainty.NOT_APPLIED,
            None,
        ),
        (
            ActionExecutionStatus.OUTCOME_UNKNOWN,
            ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            FailureCause.TOOL_EXCEPTION,
            OutcomeCertainty.UNKNOWN,
            None,
        ),
    ],
)
def test_existing_terminal_history_is_never_rewritten(
    recovery_sessions,
    execution_status,
    attempt_status,
    failure_cause,
    certainty,
    result,
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        invocation_started_at=OLD,
        execution_status=execution_status,
        attempt_status=attempt_status,
        failure_cause=failure_cause,
        outcome_certainty=certainty,
        result=result,
    )

    outcome = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert outcome.status == StaleExecutionPersistenceStatus.TERMINAL_CONFLICT
    assert execution.status == execution_status
    assert execution.result == result
    assert attempt.status == attempt_status
    assert attempt.failure_cause == failure_cause
    assert attempt.outcome_certainty == certainty
    assert events == []


def test_inconsistent_aggregate_and_attempt_are_not_mutated(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        execution_status=ActionExecutionStatus.COMPLETED,
        attempt_status=ActionExecutionAttemptStatus.RUNNING,
        result={"success": True},
    )

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.INCONSISTENT_STATE
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


def test_attempt_from_another_execution_is_rejected(recovery_sessions) -> None:
    execution_id, _, _ = seed_recovery_context(recovery_sessions)
    _, other_attempt_id, _ = seed_recovery_context(recovery_sessions)

    result = classify(recovery_sessions, execution_id, other_attempt_id)
    execution, _, events, _ = persisted_state(
        recovery_sessions, execution_id, other_attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.OWNERSHIP_MISMATCH
    assert execution.status == ActionExecutionStatus.RUNNING
    assert events == []


def test_later_attempt_makes_first_attempt_noncanonical(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)
    with recovery_sessions() as session:
        session.add(
            ActionExecutionAttemptRecord(
                execution_id=execution_id,
                attempt_number=2,
                status=ActionExecutionAttemptStatus.FAILED,
                claimed_at=OLD,
                completed_at=OLD,
                error={"code": "legacy_test"},
                failure_cause=FailureCause.LEGACY_UNCLASSIFIED,
                outcome_certainty=OutcomeCertainty.LEGACY_UNDETERMINED,
            )
        )
        session.commit()

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, attempts = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.NONCANONICAL_ATTEMPT
    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert len(attempts) == 2
    assert events == []


def test_event_failure_rolls_back_stale_classification(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    def fail_event(*args, **kwargs):
        raise RuntimeError("simulated interruption event persistence failure")

    monkeypatch.setattr(InvestigationRepository, "record_event", fail_event)

    with pytest.raises(RuntimeError, match="event persistence failure"):
        classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


def test_aggregate_cas_failure_rolls_back_attempt(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)
    monkeypatch.setattr(
        ActionExecutionRepository,
        "_transition_stale_aggregate",
        lambda *args, **kwargs: False,
    )

    result = classify(recovery_sessions, execution_id, attempt_id)
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert result.status == StaleExecutionPersistenceStatus.TRANSITION_CONFLICT
    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


def test_concurrent_stale_classification_has_one_winner_and_event(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)
    barrier = Barrier(2)
    original = ActionExecutionAttemptRepository.classify_stale_before_invocation

    def synchronized_transition(self, *args, **kwargs):
        barrier.wait(timeout=5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        ActionExecutionAttemptRepository,
        "classify_stale_before_invocation",
        synchronized_transition,
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: classify(recovery_sessions, execution_id, attempt_id),
                range(2),
            )
        )
    execution, attempt, events, attempts = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert {result.status for result in results} == {
        StaleExecutionPersistenceStatus.TRANSITIONED,
        StaleExecutionPersistenceStatus.ALREADY_CLASSIFIED,
    }
    assert execution.status == ActionExecutionStatus.FAILED
    assert attempt.status == ActionExecutionAttemptStatus.FAILED
    assert len(events) == 1
    assert len(attempts) == 1


def test_repository_classification_has_no_tool_or_model_side_effects(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    def forbidden(*args, **kwargs):
        raise AssertionError("tool or model entrypoint must not be called")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", forbidden)
    monkeypatch.setattr(InvestigationToolRegistry, "execute", forbidden)
    monkeypatch.setattr(ResponsesGateway, "create_initial", forbidden)

    result = classify(recovery_sessions, execution_id, attempt_id)

    assert result.status == StaleExecutionPersistenceStatus.TRANSITIONED


@pytest.mark.parametrize(
    ("invocation_started_at", "execution_status", "attempt_status", "certainty"),
    [
        (
            None,
            ActionExecutionStatus.FAILED,
            ActionExecutionAttemptStatus.FAILED,
            OutcomeCertainty.NOT_APPLIED,
        ),
        (
            OLD,
            ActionExecutionStatus.OUTCOME_UNKNOWN,
            ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            OutcomeCertainty.UNKNOWN,
        ),
    ],
)
def test_recovery_service_returns_canonical_stale_assessment(
    recovery_sessions,
    invocation_started_at,
    execution_status,
    attempt_status,
    certainty,
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions, invocation_started_at=invocation_started_at
    )

    result = assess(recovery_sessions, execution_id, attempt_id)

    assert result.execution.id == execution_id
    assert result.execution.status == execution_status
    assert result.attempt.id == attempt_id
    assert result.attempt.status == attempt_status
    assert result.attempt.failure_cause == FailureCause.PROCESS_INTERRUPTED
    assert result.attempt.outcome_certainty == certainty


@pytest.mark.parametrize(
    ("claimed_at", "invocation_started_at"),
    [(RECENT, None), (OLD, RECENT)],
)
def test_recovery_service_rejects_recent_durable_progress(
    recovery_sessions, claimed_at, invocation_started_at
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        claimed_at=claimed_at,
        invocation_started_at=invocation_started_at,
    )

    assert_recovery_error(
        "execution_attempt_not_stale",
        lambda: assess(recovery_sessions, execution_id, attempt_id),
    )
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )
    assert execution.status == ActionExecutionStatus.RUNNING
    assert attempt.status == ActionExecutionAttemptStatus.RUNNING
    assert events == []


def test_recovery_service_duplicate_call_preserves_timestamp_and_single_event(
    recovery_sessions,
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    first = assess(recovery_sessions, execution_id, attempt_id)
    second = assess(recovery_sessions, execution_id, attempt_id)
    _, _, events, attempts = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )

    assert first == second
    assert first.execution.completed_at == second.execution.completed_at
    assert first.attempt.completed_at == second.attempt.completed_at
    assert len(events) == 1
    assert len(attempts) == 1


def test_recovery_service_reports_missing_execution(recovery_sessions) -> None:
    assert_recovery_error(
        "action_execution_not_found",
        lambda: assess(recovery_sessions, 999, 999),
    )


def test_recovery_service_reports_missing_attempt(recovery_sessions) -> None:
    execution_id, _, _ = seed_recovery_context(recovery_sessions)
    assert_recovery_error(
        "execution_attempt_not_found",
        lambda: assess(recovery_sessions, execution_id, 999),
    )


def test_recovery_service_reports_attempt_mismatch(recovery_sessions) -> None:
    execution_id, _, _ = seed_recovery_context(recovery_sessions)
    _, other_attempt_id, _ = seed_recovery_context(recovery_sessions)
    assert_recovery_error(
        "execution_attempt_mismatch",
        lambda: assess(recovery_sessions, execution_id, other_attempt_id),
    )


def test_recovery_service_reports_noncanonical_attempt(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)
    with recovery_sessions() as session:
        session.add(
            ActionExecutionAttemptRecord(
                execution_id=execution_id,
                attempt_number=2,
                status=ActionExecutionAttemptStatus.FAILED,
                claimed_at=OLD,
                completed_at=OLD,
                error={"code": "legacy_test"},
                failure_cause=FailureCause.LEGACY_UNCLASSIFIED,
                outcome_certainty=OutcomeCertainty.LEGACY_UNDETERMINED,
            )
        )
        session.commit()

    assert_recovery_error(
        "execution_attempt_not_canonical",
        lambda: assess(recovery_sessions, execution_id, attempt_id),
    )


@pytest.mark.parametrize(
    ("execution_status", "attempt_status", "failure_cause", "certainty", "result"),
    [
        (
            ActionExecutionStatus.COMPLETED,
            ActionExecutionAttemptStatus.COMPLETED,
            None,
            OutcomeCertainty.APPLIED_ACKNOWLEDGED,
            {"success": True},
        ),
        (
            ActionExecutionStatus.FAILED,
            ActionExecutionAttemptStatus.FAILED,
            FailureCause.TOOL_REJECTED,
            OutcomeCertainty.NOT_APPLIED,
            None,
        ),
        (
            ActionExecutionStatus.OUTCOME_UNKNOWN,
            ActionExecutionAttemptStatus.OUTCOME_UNKNOWN,
            FailureCause.TOOL_EXCEPTION,
            OutcomeCertainty.UNKNOWN,
            None,
        ),
    ],
)
def test_recovery_service_preserves_other_terminal_classifications(
    recovery_sessions,
    execution_status,
    attempt_status,
    failure_cause,
    certainty,
    result,
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        invocation_started_at=OLD,
        execution_status=execution_status,
        attempt_status=attempt_status,
        failure_cause=failure_cause,
        outcome_certainty=certainty,
        result=result,
    )

    assert_recovery_error(
        "execution_attempt_already_terminal",
        lambda: assess(recovery_sessions, execution_id, attempt_id),
    )
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )
    assert execution.status == execution_status
    assert execution.result == result
    assert attempt.failure_cause == failure_cause
    assert attempt.outcome_certainty == certainty
    assert events == []


def test_recovery_service_reports_inconsistent_state(recovery_sessions) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions,
        execution_status=ActionExecutionStatus.COMPLETED,
        attempt_status=ActionExecutionAttemptStatus.RUNNING,
        result={"success": True},
    )

    assert_recovery_error(
        "execution_recovery_conflict",
        lambda: assess(recovery_sessions, execution_id, attempt_id),
    )


def test_recovery_service_concurrent_calls_return_equivalent_success(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)
    barrier = Barrier(2)
    original = ActionExecutionAttemptRepository.classify_stale_before_invocation

    def synchronized_transition(self, *args, **kwargs):
        barrier.wait(timeout=5)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(
        ActionExecutionAttemptRepository,
        "classify_stale_before_invocation",
        synchronized_transition,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: assess(recovery_sessions, execution_id, attempt_id),
                range(2),
            )
        )

    assert results[0] == results[1]
    _, _, events, _ = persisted_state(recovery_sessions, execution_id, attempt_id)
    assert len(events) == 1


def test_recovery_service_translates_terminal_race_without_rewrite(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    def terminal_wins(repository, execution_id, attempt_id, cutoff, assessed_at):
        with recovery_sessions() as winner:
            execution = winner.get(ActionExecutionRecord, execution_id)
            attempt = winner.get(ActionExecutionAttemptRecord, attempt_id)
            execution.status = ActionExecutionStatus.COMPLETED
            execution.completed_at = NOW
            execution.result = {"success": True}
            execution.completion_basis = ActionExecutionCompletionBasis.ACKNOWLEDGED_RESULT
            attempt.status = ActionExecutionAttemptStatus.COMPLETED
            attempt.completed_at = NOW
            attempt.result = {"success": True}
            attempt.outcome_certainty = OutcomeCertainty.APPLIED_ACKNOWLEDGED
            winner.commit()
        return repository._reload_after_stale_conflict(execution_id, attempt_id, cutoff)

    monkeypatch.setattr(
        ActionExecutionRepository,
        "classify_stale_interruption",
        terminal_wins,
    )
    assert_recovery_error(
        "execution_attempt_already_terminal",
        lambda: assess(recovery_sessions, execution_id, attempt_id),
    )
    execution, attempt, events, _ = persisted_state(
        recovery_sessions, execution_id, attempt_id
    )
    assert execution.status == ActionExecutionStatus.COMPLETED
    assert attempt.status == ActionExecutionAttemptStatus.COMPLETED
    assert events == []


def test_recovery_service_has_no_operational_dependencies(
    recovery_sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    execution_id, attempt_id, _ = seed_recovery_context(recovery_sessions)

    def forbidden(*args, **kwargs):
        raise AssertionError("operational dependency must not be resolved")

    monkeypatch.setattr(ActionTools, "restart_simulated_service", forbidden)
    monkeypatch.setattr(InvestigationToolRegistry, "execute", forbidden)
    monkeypatch.setattr(ResponsesGateway, "create_initial", forbidden)

    parameters = signature(ActionExecutionRecoveryService).parameters
    assert set(parameters) == {"repository", "stale_after_seconds", "current_time"}
    assert assess(recovery_sessions, execution_id, attempt_id).execution.status == (
        ActionExecutionStatus.FAILED
    )


def test_recovery_service_honors_custom_threshold(recovery_sessions) -> None:
    claimed_at = NOW - timedelta(seconds=120)
    execution_id, attempt_id, _ = seed_recovery_context(
        recovery_sessions, claimed_at=claimed_at
    )

    assert_recovery_error(
        "execution_attempt_not_stale",
        lambda: assess(
            recovery_sessions,
            execution_id,
            attempt_id,
            stale_after_seconds=180,
        ),
    )
    result = assess(
        recovery_sessions,
        execution_id,
        attempt_id,
        stale_after_seconds=60,
    )
    assert result.execution.status == ActionExecutionStatus.FAILED
