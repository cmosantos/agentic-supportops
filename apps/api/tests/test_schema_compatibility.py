from sqlalchemy import create_engine, inspect, text

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
    assert {item["name"] for item in inspector.get_columns("evidence")} >= {"origin"}
    assert {item["name"] for item in inspector.get_columns("investigation_steps")} >= {
        "origin",
        "arguments",
    }
    assert "ai_investigations" in inspector.get_table_names()
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
    engine.dispose()
