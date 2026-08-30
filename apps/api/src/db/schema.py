import json

from sqlalchemy import Engine, inspect, text


_KNOWN_PRE_MUTATION_FAILURES = {"application_not_found", "service_not_found"}


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
            if "investigation_id" not in columns:
                connection.execute(
                    text("ALTER TABLE evidence ADD COLUMN investigation_id INTEGER")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_evidence_origin "
                    "ON evidence (origin)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_evidence_investigation_id "
                    "ON evidence (investigation_id)"
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
            if "investigation_id" not in columns:
                connection.execute(
                    text(
                        "ALTER TABLE investigation_steps "
                        "ADD COLUMN investigation_id INTEGER"
                    )
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_investigation_steps_origin "
                    "ON investigation_steps (origin)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_investigation_steps_investigation_id "
                    "ON investigation_steps (investigation_id)"
                )
            )

        if "ai_investigations" in tables:
            unique_constraints = inspector.get_unique_constraints("ai_investigations")
            unique_indexes = inspector.get_indexes("ai_investigations")
            legacy_unique_shapes = (["incident_id"], ["incident_id", "mode"])
            has_legacy_incident_constraint = any(
                constraint.get("column_names") in legacy_unique_shapes
                for constraint in unique_constraints
            )
            has_legacy_incident_index = any(
                bool(index.get("unique"))
                and index.get("column_names") in legacy_unique_shapes
                and index.get("name")
                != "uq_ai_investigations_running_incident_mode"
                for index in unique_indexes
            )
            has_legacy_incident_unique = (
                has_legacy_incident_constraint or has_legacy_incident_index
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
                        "FOREIGN KEY(incident_id) REFERENCES incidents (id))"
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
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_ai_investigations_running_incident_mode "
                    "ON ai_investigations (incident_id, mode) "
                    "WHERE status = 'RUNNING'"
                )
            )

        if "action_executions" in tables:
            execution_columns = {
                column["name"]
                for column in inspector.get_columns("action_executions")
            }
            if "completion_basis" not in execution_columns:
                connection.execute(
                    text(
                        "ALTER TABLE action_executions "
                        "ADD COLUMN completion_basis VARCHAR(19)"
                    )
                )

        if "action_execution_attempts" in tables:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_action_execution_attempts_running_execution "
                    "ON action_execution_attempts (execution_id) "
                    "WHERE status = 'RUNNING'"
                )
            )
            if "action_executions" in tables:
                _backfill_action_execution_attempts(connection)


def _backfill_action_execution_attempts(connection) -> None:
    executions = connection.execute(
        text(
            "SELECT execution.id, execution.status, execution.requested_at, "
            "execution.started_at, execution.completed_at, execution.result, "
            "execution.error "
            "FROM action_executions AS execution "
            "LEFT JOIN action_execution_attempts AS attempt "
            "ON attempt.execution_id = execution.id "
            "WHERE attempt.id IS NULL "
            "ORDER BY execution.id"
        )
    ).mappings()
    for execution in executions:
        status = str(execution["status"]).upper()
        values = _legacy_attempt_values(status, execution["error"])
        connection.execute(
            text(
                "INSERT INTO action_execution_attempts "
                "(execution_id, attempt_number, status, claimed_at, "
                "invocation_started_at, completed_at, result, error, "
                "failure_cause, outcome_certainty, created_at) "
                "VALUES (:execution_id, 1, :status, :claimed_at, NULL, "
                ":completed_at, :result, :error, :failure_cause, "
                ":outcome_certainty, :created_at)"
            ),
            {
                "execution_id": execution["id"],
                "status": status,
                "claimed_at": execution["started_at"]
                or execution["requested_at"],
                "completed_at": execution["completed_at"],
                "result": execution["result"],
                "error": execution["error"],
                "failure_cause": values["failure_cause"],
                "outcome_certainty": values["outcome_certainty"],
                "created_at": execution["requested_at"]
                or execution["started_at"],
            },
        )
        if status in {"COMPLETED", "FAILED"}:
            connection.execute(
                text(
                    "UPDATE action_executions "
                    "SET completion_basis = 'LEGACY_RECORDED' "
                    "WHERE id = :execution_id AND completion_basis IS NULL"
                ),
                {"execution_id": execution["id"]},
            )


def _legacy_attempt_values(status: str, raw_error) -> dict[str, str | None]:
    if status == "COMPLETED":
        return {
            "failure_cause": None,
            "outcome_certainty": "APPLIED_ACKNOWLEDGED",
        }
    if status == "FAILED":
        error_code = _legacy_error_code(raw_error)
        if error_code in _KNOWN_PRE_MUTATION_FAILURES:
            return {
                "failure_cause": "TOOL_REJECTED",
                "outcome_certainty": "NOT_APPLIED",
            }
        return {
            "failure_cause": "LEGACY_UNCLASSIFIED",
            "outcome_certainty": "LEGACY_UNDETERMINED",
        }
    return {"failure_cause": None, "outcome_certainty": None}


def _legacy_error_code(raw_error) -> str | None:
    if raw_error is None:
        return None
    if isinstance(raw_error, str):
        try:
            raw_error = json.loads(raw_error)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_error, dict):
        return None
    code = raw_error.get("code")
    return str(code) if code is not None else None
