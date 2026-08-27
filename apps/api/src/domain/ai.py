from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domain.investigation import EvidenceRead, InvestigationStepRead


class AIInvestigationStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    FAILED = "failed"


class AIInvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AIInvestigationStatus
    summary: str = Field(min_length=1, max_length=2000)
    diagnosis: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0, le=1)
    supporting_evidence: list[str]
    recommended_next_steps: list[str]
    missing_information: list[str]


class ProviderUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    response_iterations: int = 0


class ProviderFunctionCall(BaseModel):
    call_id: str
    name: str
    arguments: str


class ResponsesTurn(BaseModel):
    response_id: str
    model: str
    function_calls: list[ProviderFunctionCall] = Field(default_factory=list)
    output_text: str = ""
    usage: ProviderUsage = Field(default_factory=ProviderUsage)


class FunctionCallOutput(BaseModel):
    call_id: str
    output: str


class AIInvestigationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    mode: str
    status: AIInvestigationStatus
    model: str
    response_id: str | None
    result: AIInvestigationResult | None
    usage: ProviderUsage
    error: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


class AIInvestigationExecution(BaseModel):
    investigation: AIInvestigationRead
    evidence: list[EvidenceRead]
    steps: list[InvestigationStepRead]
