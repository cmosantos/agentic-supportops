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

        if "ai_investigations" in tables:
            unique_constraints = inspector.get_unique_constraints("ai_investigations")
            has_legacy_incident_unique = any(
                constraint.get("column_names") == ["incident_id"]
                for constraint in unique_constraints
            )
            if has_legacy_incident_unique:
                connection.execute(
                    text(
                        "CREATE TABLE ai_investigations_m07 ("
                        "id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT, "
                        "incident_id INTEGER NOT NULL, "
                        "mode VARCHAR(20) NOT NULL, "
                        "status VARCHAR(21) NOT NULL, "
                        "model VARCHAR(100) NOT NULL, "
                        "response_id VARCHAR(200), result JSON, usage JSON NOT NULL, "
                        "error JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, "
                        "completed_at DATETIME, "
                        "FOREIGN KEY(incident_id) REFERENCES incidents (id), "
                        "UNIQUE (incident_id, mode))"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO ai_investigations_m07 "
                        "(id, incident_id, mode, status, model, response_id, result, usage, "
                        "error, created_at, completed_at) "
                        "SELECT id, incident_id, mode, status, model, response_id, result, usage, "
                        "error, created_at, completed_at FROM ai_investigations"
                    )
                )
                connection.execute(text("DROP TABLE ai_investigations"))
                connection.execute(
                    text("ALTER TABLE ai_investigations_m07 RENAME TO ai_investigations")
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_ai_investigations_incident_id "
                        "ON ai_investigations (incident_id)"
                    )
                )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_ai_investigations_status "
                        "ON ai_investigations (status)"
                    )
                )
