from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from db.models import AIInvestigationRecord, EvidenceRecord, InvestigationEventRecord, InvestigationStepRecord
from domain.ai import AIInvestigationResult, AIInvestigationStatus, InvestigationEventType, InvestigationRuntime, ProviderUsage
from domain.investigation import InvestigationOrigin, InvestigationStepStatus, ToolResult
from observability.tracing import TraceBoundary


class InvestigationRepository:
    def __init__(
        self, session: Session, tracing: TraceBoundary | None = None
    ) -> None:
        self._session = session
        self._tracing = tracing or TraceBoundary()

    def replace_start(
        self,
        incident_id: int,
        origin: InvestigationOrigin = InvestigationOrigin.DETERMINISTIC,
    ) -> None:
        self._session.execute(
            delete(EvidenceRecord).where(
                EvidenceRecord.incident_id == incident_id,
                EvidenceRecord.origin == origin,
            )
        )
        self._session.execute(
            delete(InvestigationStepRecord).where(
                InvestigationStepRecord.incident_id == incident_id,
                InvestigationStepRecord.origin == origin,
            )
        )
        self._session.commit()

    def record_result(
        self,
        incident_id: int,
        result: ToolResult,
        origin: InvestigationOrigin = InvestigationOrigin.DETERMINISTIC,
        arguments: dict | None = None,
    ) -> None:
        with self._tracing.span(
            "supportops.persistence.write",
            {
                "supportops.persistence.operation": "record_tool_result",
                "supportops.incident_id": incident_id,
                "supportops.tool.name": result.tool,
            },
        ):
            self._record_result(incident_id, result, origin, arguments)

    def _record_result(
        self,
        incident_id: int,
        result: ToolResult,
        origin: InvestigationOrigin,
        arguments: dict | None,
    ) -> None:
        status = (
            InvestigationStepStatus.COMPLETED
            if result.success
            else InvestigationStepStatus.FAILED
        )
        payload = result.model_dump(mode="json")
        self._session.add(
            InvestigationStepRecord(
                incident_id=incident_id,
                tool=result.tool,
                target_resource=result.resource,
                origin=origin,
                arguments=arguments or {},
                status=status,
                result=payload,
                completed_at=datetime.now(timezone.utc),
            )
        )
        if result.success:
            self._session.add(
                EvidenceRecord(
                    incident_id=incident_id,
                    source=result.tool,
                    resource=result.resource,
                    origin=origin,
                    payload=result.data or {},
                )
            )
        self._session.commit()

    def list_evidence(
        self,
        incident_id: int,
        origin: InvestigationOrigin = InvestigationOrigin.DETERMINISTIC,
    ) -> list[EvidenceRecord]:
        statement = (
            select(EvidenceRecord)
            .where(
                EvidenceRecord.incident_id == incident_id,
                EvidenceRecord.origin == origin,
            )
            .order_by(EvidenceRecord.id)
        )
        return list(self._session.scalars(statement))

    def list_steps(
        self,
        incident_id: int,
        origin: InvestigationOrigin = InvestigationOrigin.DETERMINISTIC,
    ) -> list[InvestigationStepRecord]:
        statement = (
            select(InvestigationStepRecord)
            .where(
                InvestigationStepRecord.incident_id == incident_id,
                InvestigationStepRecord.origin == origin,
            )
            .order_by(InvestigationStepRecord.id)
        )
        return list(self._session.scalars(statement))

    def start_ai_run(
        self, incident_id: int, model: str, mode: str = "ai"
    ) -> AIInvestigationRecord:
        with self._tracing.span(
            "supportops.persistence.write",
            {
                "supportops.persistence.operation": "start_investigation",
                "supportops.incident_id": incident_id,
                "supportops.runtime": (
                    "agents_sdk" if mode == "agents_sdk" else "manual_responses"
                ),
            },
        ):
            return self._start_ai_run(incident_id, model, mode)

    def _start_ai_run(
        self, incident_id: int, model: str, mode: str
    ) -> AIInvestigationRecord:
        existing = self._session.scalar(
            select(AIInvestigationRecord).where(
                AIInvestigationRecord.incident_id == incident_id,
                AIInvestigationRecord.mode == mode,
            )
        )
        if existing is not None:
            self._session.execute(
                delete(InvestigationEventRecord).where(
                    InvestigationEventRecord.investigation_id == existing.id
                )
            )
            self._session.delete(existing)
            self._session.flush()
        record = AIInvestigationRecord(
            incident_id=incident_id,
            mode=mode,
            status=AIInvestigationStatus.RUNNING,
            model=model,
            usage=ProviderUsage(
                runtime="agents_sdk" if mode == "agents_sdk" else "manual_responses"
            ).model_dump(),
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return record

    def complete_ai_run(
        self,
        record: AIInvestigationRecord,
        result: AIInvestigationResult,
        response_id: str | None,
        usage: ProviderUsage,
    ) -> AIInvestigationRecord:
        with self._tracing.span(
            "supportops.persistence.write",
            {
                "supportops.persistence.operation": "complete_investigation",
                "supportops.investigation_id": record.id,
            },
        ):
            return self._complete_ai_run(record, result, response_id, usage)

    def _complete_ai_run(
        self,
        record: AIInvestigationRecord,
        result: AIInvestigationResult,
        response_id: str | None,
        usage: ProviderUsage,
    ) -> AIInvestigationRecord:
        record.status = result.status
        record.result = result.model_dump(mode="json")
        record.response_id = response_id
        record.usage = usage.model_dump()
        record.error = None
        record.completed_at = datetime.now(timezone.utc)
        self._session.commit()
        self._session.refresh(record)
        return record

    def fail_ai_run(
        self,
        record: AIInvestigationRecord,
        code: str,
        message: str,
        response_id: str | None = None,
        usage: ProviderUsage | None = None,
    ) -> None:
        with self._tracing.span(
            "supportops.persistence.write",
            {
                "supportops.persistence.operation": "fail_investigation",
                "supportops.investigation_id": record.id,
            },
        ):
            self._fail_ai_run(record, code, message, response_id, usage)

    def _fail_ai_run(
        self,
        record: AIInvestigationRecord,
        code: str,
        message: str,
        response_id: str | None,
        usage: ProviderUsage | None,
    ) -> None:
        record.status = AIInvestigationStatus.FAILED
        record.response_id = response_id
        record.usage = (usage or ProviderUsage()).model_dump()
        record.error = {"code": code, "message": message}
        record.completed_at = datetime.now(timezone.utc)
        self._session.commit()

    def get_ai_run(
        self, incident_id: int, mode: str = "ai"
    ) -> AIInvestigationRecord | None:
        return self._session.scalar(
            select(AIInvestigationRecord).where(
                AIInvestigationRecord.incident_id == incident_id,
                AIInvestigationRecord.mode == mode,
            )
        )

    def record_event(
        self,
        investigation_id: int,
        runtime: InvestigationRuntime,
        event_type: InvestigationEventType,
        sequence: int,
        **fields,
    ) -> InvestigationEventRecord:
        metadata = fields.pop("metadata", {})
        record = InvestigationEventRecord(
            investigation_id=investigation_id,
            runtime=runtime,
            event_type=event_type,
            sequence=sequence,
            event_metadata=metadata,
            timestamp=datetime.now(timezone.utc),
            **fields,
        )
        self._session.add(record)
        self._session.commit()
        self._session.refresh(record)
        return record

    def next_event_sequence(self, investigation_id: int) -> int:
        current = self._session.scalar(
            select(func.max(InvestigationEventRecord.sequence)).where(
                InvestigationEventRecord.investigation_id == investigation_id
            )
        )
        return (current or 0) + 1

    def list_events(self, investigation_id: int) -> list[InvestigationEventRecord]:
        with self._tracing.span(
            "supportops.persistence.read",
            {
                "supportops.persistence.operation": "load_timeline",
                "supportops.investigation_id": investigation_id,
            },
        ):
            return list(
                self._session.scalars(
                    select(InvestigationEventRecord)
                    .where(
                        InvestigationEventRecord.investigation_id == investigation_id
                    )
                    .order_by(InvestigationEventRecord.sequence)
                )
            )
