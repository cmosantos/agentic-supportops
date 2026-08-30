import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from db.base import Base
from db.schema import ensure_sqlite_schema_compatibility


def test_legacy_sqlite_schema_is_upgraded_without_losing_data(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE evidence ("
                "id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, "
                "source VARCHAR(100) NOT NULL, resource VARCHAR(100) NOT NULL, "
                "payload JSON NOT NULL, created_at DATETIME NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE investigation_steps ("
                "id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, "
                "tool VARCHAR(100) NOT NULL, target_resource VARCHAR(100) NOT NULL, "
                "status VARCHAR(20) NOT NULL, result JSON NOT NULL, "
                "created_at DATETIME NOT NULL, completed_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO evidence VALUES "
                "(1, 7, 'get_device', 'WS-001', '{}', CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO investigation_steps VALUES "
                "(1, 7, 'get_device', 'WS-001', 'COMPLETED', '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    ensure_sqlite_schema_compatibility(engine)
    Base.metadata.create_all(engine)
    ensure_sqlite_schema_compatibility(engine)

    inspector = inspect(engine)
    assert {item["name"] for item in inspector.get_columns("evidence")} >= {
        "origin",
        "investigation_id",
    }
    assert {item["name"] for item in inspector.get_columns("investigation_steps")} >= {
        "origin",
        "arguments",
        "investigation_id",
    }
    assert "ai_investigations" in inspector.get_table_names()
    assert "investigation_events" in inspector.get_table_names()
    with engine.connect() as connection:
        evidence = connection.execute(
            text("SELECT id, origin FROM evidence")
        ).one()
        step = connection.execute(
            text("SELECT id, origin, arguments FROM investigation_steps")
        ).one()
    assert evidence == (1, "DETERMINISTIC")
    assert step == (1, "DETERMINISTIC", "{}")
    engine.dispose()


def test_fresh_sqlite_database_remains_supported(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    ensure_sqlite_schema_compatibility(engine)
    Base.metadata.create_all(engine)
    assert "ai_investigations" in inspect(engine).get_table_names()
    assert "investigation_events" in inspect(engine).get_table_names()
    assert "action_proposals" in inspect(engine).get_table_names()
    assert "action_execution_attempts" in inspect(engine).get_table_names()
    assert "action_execution_reconciliations" in inspect(engine).get_table_names()
    assert "completion_basis" in {
        column["name"] for column in inspect(engine).get_columns("action_executions")
    }
    attempt_constraints = inspect(engine).get_unique_constraints(
        "action_execution_attempts"
    )
    assert any(
        item["column_names"] == ["execution_id", "attempt_number"]
        for item in attempt_constraints
    )
    running_index = next(
        item
        for item in inspect(engine).get_indexes("action_execution_attempts")
        if item["name"] == "uq_action_execution_attempts_running_execution"
    )
    assert bool(running_index["unique"]) is True
    assert running_index["column_names"] == ["execution_id"]
    assert any(
        item["column_names"] == ["attempt_id"]
        for item in inspect(engine).get_unique_constraints(
            "action_execution_reconciliations"
        )
    )
    engine.dispose()


def _legacy_execution_engine(tmp_path, name: str, rows: list[dict]):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE action_executions ("
                "id INTEGER PRIMARY KEY, proposal_id INTEGER NOT NULL UNIQUE, "
                "incident_id INTEGER NOT NULL, capability_name VARCHAR(100) NOT NULL, "
                "status VARCHAR(20) NOT NULL, requested_at DATETIME NOT NULL, "
                "started_at DATETIME NOT NULL, completed_at DATETIME, "
                "result JSON, error JSON)"
            )
        )
        for row in rows:
            connection.execute(
                text(
                    "INSERT INTO action_executions "
                    "(id, proposal_id, incident_id, capability_name, status, "
                    "requested_at, started_at, completed_at, result, error) "
                    "VALUES (:id, :proposal_id, 23, 'restart_simulated_service', "
                    ":status, '2026-08-29 12:00:00', '2026-08-29 12:00:01', "
                    ":completed_at, :result, :error)"
                ),
                row,
            )
    ensure_sqlite_schema_compatibility(engine)
    Base.metadata.create_all(engine)
    ensure_sqlite_schema_compatibility(engine)
    return engine


def _attempt_and_execution(engine, execution_id: int = 1):
    with engine.connect() as connection:
        attempt = connection.execute(
            text(
                "SELECT attempt_number, status, claimed_at, invocation_started_at, "
                "completed_at, result, error, failure_cause, outcome_certainty "
                "FROM action_execution_attempts WHERE execution_id=:execution_id"
            ),
            {"execution_id": execution_id},
        ).one()
        basis = connection.execute(
            text(
                "SELECT completion_basis FROM action_executions "
                "WHERE id=:execution_id"
            ),
            {"execution_id": execution_id},
        ).scalar_one()
    return attempt, basis


def test_legacy_completed_execution_receives_acknowledged_attempt(tmp_path) -> None:
    engine = _legacy_execution_engine(
        tmp_path,
        "legacy-completed.db",
        [
            {
                "id": 1,
                "proposal_id": 101,
                "status": "COMPLETED",
                "completed_at": "2026-08-29 12:00:02",
                "result": '{"success":true,"data":{"current_state":"healthy"}}',
                "error": None,
            }
        ],
    )

    attempt, basis = _attempt_and_execution(engine)

    assert attempt.attempt_number == 1
    assert attempt.status == "COMPLETED"
    assert attempt.result == '{"success":true,"data":{"current_state":"healthy"}}'
    assert attempt.failure_cause is None
    assert attempt.outcome_certainty == "APPLIED_ACKNOWLEDGED"
    assert basis == "LEGACY_RECORDED"
    engine.dispose()


def test_legacy_known_and_ambiguous_failures_preserve_certainty(tmp_path) -> None:
    engine = _legacy_execution_engine(
        tmp_path,
        "legacy-failed.db",
        [
            {
                "id": 1,
                "proposal_id": 101,
                "status": "FAILED",
                "completed_at": "2026-08-29 12:00:02",
                "result": None,
                "error": '{"code":"application_not_found","message":"missing"}',
            },
            {
                "id": 2,
                "proposal_id": 102,
                "status": "FAILED",
                "completed_at": "2026-08-29 12:00:03",
                "result": None,
                "error": '{"code":"service_not_found","message":"missing"}',
            },
            {
                "id": 3,
                "proposal_id": 103,
                "status": "FAILED",
                "completed_at": "2026-08-29 12:00:04",
                "result": None,
                "error": '{"code":"capability_failure","message":"failed"}',
            },
        ],
    )

    known, known_basis = _attempt_and_execution(engine, 1)
    known_service, known_service_basis = _attempt_and_execution(engine, 2)
    ambiguous, ambiguous_basis = _attempt_and_execution(engine, 3)

    assert known.status == "FAILED"
    assert known.failure_cause == "TOOL_REJECTED"
    assert known.outcome_certainty == "NOT_APPLIED"
    assert known_basis == "LEGACY_RECORDED"
    assert known_service.status == "FAILED"
    assert known_service.failure_cause == "TOOL_REJECTED"
    assert known_service.outcome_certainty == "NOT_APPLIED"
    assert known_service_basis == "LEGACY_RECORDED"
    assert ambiguous.status == "FAILED"
    assert ambiguous.failure_cause == "LEGACY_UNCLASSIFIED"
    assert ambiguous.outcome_certainty == "LEGACY_UNDETERMINED"
    assert ambiguous_basis == "LEGACY_RECORDED"
    engine.dispose()


def test_legacy_running_execution_remains_running_without_fabricated_outcome(
    tmp_path,
) -> None:
    engine = _legacy_execution_engine(
        tmp_path,
        "legacy-running.db",
        [
            {
                "id": 1,
                "proposal_id": 101,
                "status": "RUNNING",
                "completed_at": None,
                "result": None,
                "error": None,
            }
        ],
    )

    attempt, basis = _attempt_and_execution(engine)

    assert attempt.status == "RUNNING"
    assert attempt.claimed_at == "2026-08-29 12:00:01"
    assert attempt.invocation_started_at is None
    assert attempt.completed_at is None
    assert attempt.failure_cause is None
    assert attempt.outcome_certainty is None
    assert basis is None
    engine.dispose()


def test_execution_attempt_backfill_is_idempotent(tmp_path) -> None:
    engine = _legacy_execution_engine(
        tmp_path,
        "legacy-idempotent.db",
        [
            {
                "id": 1,
                "proposal_id": 101,
                "status": "COMPLETED",
                "completed_at": "2026-08-29 12:00:02",
                "result": '{"success":true}',
                "error": None,
            }
        ],
    )
    with engine.connect() as connection:
        before = connection.execute(
            text("SELECT * FROM action_execution_attempts")
        ).one()

    ensure_sqlite_schema_compatibility(engine)
    Base.metadata.create_all(engine)
    ensure_sqlite_schema_compatibility(engine)

    with engine.connect() as connection:
        after = connection.execute(
            text("SELECT * FROM action_execution_attempts")
        ).one()
        count = connection.execute(
            text("SELECT COUNT(*) FROM action_execution_attempts")
        ).scalar_one()
    running_indexes = [
        item
        for item in inspect(engine).get_indexes("action_execution_attempts")
        if item["name"] == "uq_action_execution_attempts_running_execution"
    ]
    assert after == before
    assert count == 1
    assert len(running_indexes) == 1
    engine.dispose()


def test_execution_attempt_and_reconciliation_constraints_are_database_backed(
    tmp_path,
) -> None:
    engine = _legacy_execution_engine(
        tmp_path,
        "attempt-constraints.db",
        [
            {
                "id": 1,
                "proposal_id": 101,
                "status": "FAILED",
                "completed_at": "2026-08-29 12:00:02",
                "result": None,
                "error": '{"code":"application_not_found"}',
            }
        ],
    )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO action_execution_attempts "
                    "(execution_id, attempt_number, status, claimed_at, created_at) "
                    "VALUES (1, 1, 'FAILED', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO action_execution_attempts "
                "(execution_id, attempt_number, status, claimed_at, created_at) "
                "VALUES (1, 2, 'RUNNING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO action_execution_attempts "
                    "(execution_id, attempt_number, status, claimed_at, created_at) "
                    "VALUES (1, 3, 'RUNNING', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )

    with engine.begin() as connection:
        running_attempt = connection.execute(
            text(
                "SELECT id FROM action_execution_attempts "
                "WHERE execution_id=1 AND attempt_number=2"
            )
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO action_execution_reconciliations "
                "(attempt_id, execution_id, status, observer, expected_outcome, "
                "requested_at, started_at) "
                "VALUES (:attempt_id, 1, 'RUNNING', 'get_application_health', "
                "'{\"state\":\"healthy\"}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"attempt_id": running_attempt},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO action_execution_reconciliations "
                    "(attempt_id, execution_id, status, observer, expected_outcome, "
                    "requested_at, started_at) "
                    "VALUES (:attempt_id, 1, 'INCONCLUSIVE', "
                    "'get_application_health', '{}', CURRENT_TIMESTAMP, "
                    "CURRENT_TIMESTAMP)"
                ),
                {"attempt_id": running_attempt},
            )
    engine.dispose()


def test_legacy_ai_run_uniqueness_becomes_runtime_specific(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-ai.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE incidents (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO incidents VALUES (19)"))
        connection.execute(
            text(
                "CREATE TABLE ai_investigations ("
                "id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL UNIQUE, "
                "mode VARCHAR(20) NOT NULL, status VARCHAR(21) NOT NULL, "
                "model VARCHAR(100) NOT NULL, response_id VARCHAR(200), result JSON, "
                "usage JSON NOT NULL, error JSON, created_at DATETIME NOT NULL, "
                "completed_at DATETIME)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ai_investigations VALUES "
                "(1, 19, 'ai', 'COMPLETED', 'gpt-4.1-mini', 'resp-existing', "
                "NULL, '{}', NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    ensure_sqlite_schema_compatibility(engine)
    ensure_sqlite_schema_compatibility(engine)
    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT mode, response_id FROM ai_investigations WHERE incident_id=19")
        ).one()
        connection.execute(
            text(
                "INSERT INTO ai_investigations "
                "(incident_id, mode, status, model, usage, created_at) VALUES "
                "(19, 'agents_sdk', 'RUNNING', 'gpt-4.1-mini', '{}', CURRENT_TIMESTAMP)"
            )
        )
        count = connection.execute(
            text("SELECT COUNT(*) FROM ai_investigations WHERE incident_id=19")
        ).scalar_one()
    assert existing == ("ai", "resp-existing")
    assert count == 2
    engine.dispose()


def test_legacy_ai_unique_index_shape_is_migrated_idempotently(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-ai-index.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE incidents (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO incidents VALUES (19)"))
        connection.execute(
            text(
                "CREATE TABLE ai_investigations ("
                "id INTEGER PRIMARY KEY, incident_id INTEGER NOT NULL, "
                "mode VARCHAR(20) NOT NULL, status VARCHAR(21) NOT NULL, "
                "model VARCHAR(100) NOT NULL, response_id VARCHAR(200), result JSON, "
                "usage JSON NOT NULL, error JSON, created_at DATETIME NOT NULL, "
                "completed_at DATETIME, "
                "FOREIGN KEY(incident_id) REFERENCES incidents(id))"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX arbitrary_legacy_unique_name "
                "ON ai_investigations (incident_id)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX arbitrary_status_index "
                "ON ai_investigations (status)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ai_investigations VALUES "
                "(7, 19, 'ai', 'COMPLETED', 'gpt-4.1-mini', 'resp-manual', "
                ":result, :usage, NULL, "
                "'2026-08-27 10:00:00', '2026-08-27 10:01:00')"
            ),
            {
                "result": '{"diagnosis":"DNS failure"}',
                "usage": (
                    '{"total_tokens":2960,"runtime":"manual_responses"}'
                ),
            },
        )

    legacy_inspector = inspect(engine)
    assert legacy_inspector.get_unique_constraints("ai_investigations") == []
    assert any(
        bool(item["unique"]) and item["column_names"] == ["incident_id"]
        for item in legacy_inspector.get_indexes("ai_investigations")
    )

    ensure_sqlite_schema_compatibility(engine)

    with engine.connect() as connection:
        preserved = connection.execute(
            text(
                "SELECT id, incident_id, mode, status, model, response_id, result, "
                "usage, error, created_at, completed_at FROM ai_investigations"
            )
        ).one()
    assert preserved == (
        7,
        19,
        "ai",
        "COMPLETED",
        "gpt-4.1-mini",
        "resp-manual",
        '{"diagnosis":"DNS failure"}',
        '{"total_tokens":2960,"runtime":"manual_responses"}',
        None,
        "2026-08-27 10:00:00",
        "2026-08-27 10:01:00",
    )

    migrated_inspector = inspect(engine)
    migrated_indexes = migrated_inspector.get_indexes("ai_investigations")
    migrated_constraints = migrated_inspector.get_unique_constraints(
        "ai_investigations"
    )
    assert not any(
        bool(item.get("unique"))
        and item.get("column_names") == ["incident_id"]
        for item in migrated_indexes
    )
    assert not any(
        item.get("column_names") in (["incident_id"], ["incident_id", "mode"])
        for item in migrated_constraints
    )
    running_index = next(
        item
        for item in migrated_indexes
        if item.get("name") == "uq_ai_investigations_running_incident_mode"
    )
    assert bool(running_index["unique"]) is True
    assert running_index["column_names"] == ["incident_id", "mode"]

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ai_investigations "
                "(incident_id, mode, status, model, usage, created_at) VALUES "
                "(19, 'agents_sdk', 'RUNNING', 'gpt-4.1-mini', '{}', CURRENT_TIMESTAMP)"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ai_investigations "
                    "(incident_id, mode, status, model, usage, created_at) VALUES "
                    "(19, 'agents_sdk', 'RUNNING', 'gpt-4.1-mini', '{}', CURRENT_TIMESTAMP)"
                )
            )

    with engine.connect() as connection:
        rows_before_repeat = connection.execute(
            text(
                "SELECT id, incident_id, mode, response_id FROM ai_investigations "
                "ORDER BY id"
            )
        ).all()
    ensure_sqlite_schema_compatibility(engine)
    with engine.connect() as connection:
        rows_after_repeat = connection.execute(
            text(
                "SELECT id, incident_id, mode, response_id FROM ai_investigations "
                "ORDER BY id"
            )
        ).all()
    assert rows_after_repeat == rows_before_repeat
    assert len(rows_after_repeat) == 2
    engine.dispose()
