from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from domain.incident import IncidentPriority, IncidentStatus
from domain.investigation import InvestigationStepStatus


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
    status: Mapped[InvestigationStepStatus] = mapped_column(
        Enum(InvestigationStepStatus, native_enum=False), index=True
    )
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
