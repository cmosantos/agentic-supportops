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
