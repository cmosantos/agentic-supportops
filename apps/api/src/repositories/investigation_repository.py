from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import EvidenceRecord, InvestigationStepRecord
from domain.investigation import InvestigationStepStatus, ToolResult


class InvestigationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def replace_start(self, incident_id: int) -> None:
        self._session.execute(delete(EvidenceRecord).where(EvidenceRecord.incident_id == incident_id))
        self._session.execute(
            delete(InvestigationStepRecord).where(InvestigationStepRecord.incident_id == incident_id)
        )
        self._session.commit()

    def record_result(self, incident_id: int, result: ToolResult) -> None:
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
                    payload=result.data or {},
                )
            )
        self._session.commit()

    def list_evidence(self, incident_id: int) -> list[EvidenceRecord]:
        statement = (
            select(EvidenceRecord)
            .where(EvidenceRecord.incident_id == incident_id)
            .order_by(EvidenceRecord.id)
        )
        return list(self._session.scalars(statement))

    def list_steps(self, incident_id: int) -> list[InvestigationStepRecord]:
        statement = (
            select(InvestigationStepRecord)
            .where(InvestigationStepRecord.incident_id == incident_id)
            .order_by(InvestigationStepRecord.id)
        )
        return list(self._session.scalars(statement))
