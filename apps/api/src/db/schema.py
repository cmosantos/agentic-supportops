from sqlalchemy import Engine, inspect, text


def ensure_sqlite_schema_compatibility(engine: Engine) -> None:
    """Apply the small, idempotent schema evolution required by Mission 04."""
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())

        if "evidence" in tables:
            columns = {column["name"] for column in inspector.get_columns("evidence")}
            if "origin" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE evidence ADD COLUMN origin "
                        "VARCHAR(13) NOT NULL DEFAULT 'DETERMINISTIC'"
                    )
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_evidence_origin "
                    "ON evidence (origin)"
                )
            )

        if "investigation_steps" in tables:
            columns = {
                column["name"]
                for column in inspector.get_columns("investigation_steps")
            }
            if "origin" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE investigation_steps ADD COLUMN origin "
                        "VARCHAR(13) NOT NULL DEFAULT 'DETERMINISTIC'"
                    )
                )
            if "arguments" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE investigation_steps ADD COLUMN arguments "
                        "JSON NOT NULL DEFAULT '{}'"
                    )
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_investigation_steps_origin "
                    "ON investigation_steps (origin)"
                )
            )
