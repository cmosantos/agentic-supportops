from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ActionType(StrEnum):
    RESTART_SIMULATED_SERVICE = "restart_simulated_service"
    UNLOCK_SIMULATED_USER = "unlock_simulated_user"
    RESET_SIMULATED_APPLICATION_STATE = "reset_simulated_application_state"


class ActionRisk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ActionProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[int] = Field(min_length=1)
    risk_level: ActionRisk


class RestartServiceParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_name: str = Field(min_length=1, max_length=100)


class NoActionParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelActionProposalBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=2000)
    supporting_evidence_ids: list[int] = Field(min_length=1)
    risk_level: ActionRisk


class RestartServiceProposal(ModelActionProposalBase):
    action_type: Literal["restart_simulated_service"]
    parameters: RestartServiceParameters


class UnlockUserProposal(ModelActionProposalBase):
    action_type: Literal["unlock_simulated_user"]
    parameters: NoActionParameters


class ResetApplicationProposal(ModelActionProposalBase):
    action_type: Literal["reset_simulated_application_state"]
    parameters: NoActionParameters


ModelActionProposal = Annotated[
    RestartServiceProposal | UnlockUserProposal | ResetApplicationProposal,
    Field(discriminator="action_type"),
]


class ActionProposalRead(ActionProposalCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    investigation_id: int
    incident_id: int
    approval_status: ApprovalStatus
    created_at: datetime
    decision_at: datetime | None
    rejection_reason: str | None


class ActionRejection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)
