from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from domain.incident import IncidentPriority, IncidentStatus
from domain.investigation import InvestigationOrigin, InvestigationStepStatus
from domain.ai import AIInvestigationStatus, InvestigationEventType, InvestigationRuntime


class IncidentRecord(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(100))
    priority: Mapped[IncidentPriority] = mapped_column(
        Enum(IncidentPriority, native_enum=False), index=True
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, native_enum=False), index=True
    )
    requester: Mapped[str] = mapped_column(String(200))
    catalog_id: Mapped[str | None] = mapped_column(String(20), unique=True, index=True)
    affected_resource_type: Mapped[str | None] = mapped_column(String(50))
    affected_resource_id: Mapped[str | None] = mapped_column(String(100))
    investigation_context: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    source: Mapped[str] = mapped_column(String(100))
    resource: Mapped[str] = mapped_column(String(100))
    origin: Mapped[InvestigationOrigin] = mapped_column(
        Enum(InvestigationOrigin, native_enum=False),
        default=InvestigationOrigin.DETERMINISTIC,
        index=True,
    )
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class InvestigationStepRecord(Base):
    __tablename__ = "investigation_steps"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id"), index=True)
    tool: Mapped[str] = mapped_column(String(100))
    target_resource: Mapped[str] = mapped_column(String(100))
    origin: Mapped[InvestigationOrigin] = mapped_column(
        Enum(InvestigationOrigin, native_enum=False),
        default=InvestigationOrigin.DETERMINISTIC,
        index=True,
    )
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[InvestigationStepStatus] = mapped_column(
        Enum(InvestigationStepStatus, native_enum=False), index=True
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIInvestigationRecord(Base):
    __tablename__ = "ai_investigations"
    __table_args__ = (UniqueConstraint("incident_id", "mode"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id"), index=True
    )
    mode: Mapped[str] = mapped_column(String(20), default="ai")
    status: Mapped[AIInvestigationStatus] = mapped_column(
        Enum(AIInvestigationStatus, native_enum=False), index=True
    )
    model: Mapped[str] = mapped_column(String(100))
    response_id: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[dict | None] = mapped_column(JSON)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvestigationEventRecord(Base):
    __tablename__ = "investigation_events"
    __table_args__ = (UniqueConstraint("investigation_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    investigation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_investigations.id"), index=True
    )
    runtime: Mapped[InvestigationRuntime] = mapped_column(
        Enum(InvestigationRuntime, native_enum=False), index=True
    )
    event_type: Mapped[InvestigationEventType] = mapped_column(
        Enum(InvestigationEventType, native_enum=False), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    model_turn: Mapped[int | None] = mapped_column(Integer)
    tool_name: Mapped[str | None] = mapped_column(String(100))
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    arguments: Mapped[dict | None] = mapped_column(JSON)
    result_summary: Mapped[str | None] = mapped_column(Text)
    response_id: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str | None] = mapped_column(String(30))
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
