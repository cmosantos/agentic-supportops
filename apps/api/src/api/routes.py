from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from agents import Model

from api.dependencies import get_agents_sdk_model, get_controlled_tools, get_db_session, get_responses_gateway, get_trace_boundary
from core.config import settings
from domain.ai import AIInvestigationExecution, AIInvestigationRead, InvestigationEventRead, InvestigationRuntime
from db.models import IncidentRecord
from domain.incident import IncidentCreate, IncidentRead
from domain.investigation import (
    EvidenceRead,
    InvestigationOrigin,
    InvestigationRead,
    InvestigationStepRead,
)
from domain.action_proposal import (
    ActionProposalCreate,
    ActionProposalRead,
    ActionRejection,
)
from domain.action_execution import (
    ActionExecutionRead,
    ActionExecutionStaleAssessmentRead,
)
from domain.action_execution_reconciliation import (
    ActionExecutionReconciliationOperationalRead,
    ActionExecutionReconciliationRead,
)
from domain.outcome_verification import OutcomeVerificationRead
from domain.incident_resolution import IncidentResolutionCreate, IncidentResolutionRead
from repositories.incident_repository import IncidentRepository
from repositories.investigation_repository import ActiveInvestigationExistsError, InvestigationRepository
from repositories.action_proposal_repository import (
    ActionProposalRepository,
    InvalidApprovalTransitionError,
)
from repositories.action_execution_repository import ActionExecutionRepository
from repositories.action_execution_reconciliation_repository import (
    ActionExecutionReconciliationRepository,
)
from repositories.outcome_verification_repository import OutcomeVerificationRepository
from repositories.incident_resolution_repository import IncidentResolutionRepository
from services.incident_service import IncidentService
from integrations.responses_gateway import ResponsesGateway
from integrations.mcp_client import build_investigation_tools
from services.ai_investigation_service import AIInvestigationError, AIInvestigationService
from services.agents_sdk_investigation_service import AgentsSDKInvestigationService
from services.action_proposal_service import (
    ActionProposalEligibilityError,
    ActionProposalEvidenceError,
    ActionProposalNotFoundError,
    ActionProposalService,
    InvalidActionProposalError,
    InvalidActionTypeError,
)
from services.action_execution_service import (
    ActionExecutionNotApprovedError,
    ActionExecutionNotFoundError,
    ActionExecutionQueryService,
    ActionExecutionService,
)
from services.action_execution_recovery_service import (
    ActionExecutionRecoveryError,
    ActionExecutionRecoveryService,
)
from services.action_execution_reconciliation_service import (
    ActionExecutionReconciliationError,
    ActionExecutionReconciliationService,
)
from services.execution_policy import ExecutionPolicy, ExecutionPolicyDeniedError
from services.outcome_verification_service import (
    ActionExecutionNotCompletedError,
    ActionExecutionNotFoundError as VerificationExecutionNotFoundError,
    OutcomeVerificationNotFoundError,
    OutcomeVerificationService,
)
from services.verification_policy import VerificationPolicy, VerificationPolicyDeniedError
from services.incident_resolution_service import (
    IncidentResolutionService,
    ResolutionDecisionConflictError,
    ResolutionIncidentNotFoundError,
    ResolutionNotEligibleError,
    ResolutionOwnershipError,
    ResolutionVerificationNotFoundError,
)
from services.tool_registry import InvestigationToolRegistry
from services.investigation_service import (
    InvalidInvestigationContextError,
    InvestigationService,
    UnsupportedInvestigationError,
)
from observability.tracing import TraceBoundary

router = APIRouter()
DatabaseSession = Annotated[Session, Depends(get_db_session)]
ResponsesClientDependency = Annotated[ResponsesGateway | None, Depends(get_responses_gateway)]
AgentsSDKModelDependency = Annotated[Model | None, Depends(get_agents_sdk_model)]
TraceBoundaryDependency = Annotated[TraceBoundary, Depends(get_trace_boundary)]
ControlledToolsDependency = Annotated[InvestigationToolRegistry, Depends(get_controlled_tools)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "agentic-supportops-api"}


@router.post("/incidents", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentCreate, session: DatabaseSession) -> IncidentRead:
    return IncidentService(IncidentRepository(session)).create(payload)


@router.get("/incidents", response_model=list[IncidentRead])
def list_incidents(session: DatabaseSession) -> list[IncidentRead]:
    return IncidentService(IncidentRepository(session)).list_all()


@router.get(
    "/incidents/{incident_id}",
    response_model=IncidentRead,
    responses={status.HTTP_404_NOT_FOUND: {"description": "Incident not found"}},
)
def get_incident(incident_id: str, session: DatabaseSession) -> IncidentRead:
    incident = _incident_or_404(incident_id, session)
    return IncidentRead.model_validate(incident)


@router.post("/incidents/{incident_id}/investigate", response_model=InvestigationRead)
def investigate_incident(incident_id: str, session: DatabaseSession) -> InvestigationRead:
    incident = _incident_or_404(incident_id, session)
    service = InvestigationService(InvestigationRepository(session))
    try:
        return service.investigate(incident)
    except (UnsupportedInvestigationError, InvalidInvestigationContextError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "investigation_not_supported", "message": str(error)},
        ) from error


@router.get("/incidents/{incident_id}/evidence", response_model=list[EvidenceRead])
def get_incident_evidence(incident_id: str, session: DatabaseSession) -> list[EvidenceRead]:
    incident = _incident_or_404(incident_id, session)
    return InvestigationService(InvestigationRepository(session)).get_evidence(incident)


@router.get("/incidents/{incident_id}/investigation", response_model=InvestigationRead)
def get_incident_investigation(
    incident_id: str, session: DatabaseSession
) -> InvestigationRead:
    incident = _incident_or_404(incident_id, session)
    return InvestigationService(InvestigationRepository(session)).get_investigation(incident)


@router.get("/ai/config")
def get_ai_configuration() -> dict[str, str | bool]:
    return {"configured": bool(settings.openai_api_key), "model": settings.openai_model}


@router.post("/incidents/{incident_id}/investigate-ai", response_model=AIInvestigationExecution)
def investigate_incident_with_ai(
    incident_id: str,
    session: DatabaseSession,
    gateway: ResponsesClientDependency,
    tracing: TraceBoundaryDependency,
) -> AIInvestigationExecution:
    incident = _incident_or_404(incident_id, session)
    if gateway is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ai_not_configured", "message": "OpenAI is not configured"},
        )
    service = AIInvestigationService(
        repository=InvestigationRepository(session, tracing),
        tools=_investigation_tools(),
        gateway=gateway,
        max_response_iterations=settings.ai_max_response_iterations,
        max_tool_calls=settings.ai_max_tool_calls,
        max_identical_tool_calls=settings.ai_max_identical_tool_calls,
        tracing=tracing,
    )
    with tracing.span(
        "api.investigation.request",
        {
            "supportops.incident_reference": incident.catalog_id or incident_id,
            "supportops.runtime": "manual_responses",
        },
    ):
        try:
            return service.investigate(incident)
        except ActiveInvestigationExistsError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "investigation_already_running", "message": str(error)},
            ) from error
        except AIInvestigationError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": error.code, "message": str(error)},
            ) from error


@router.get("/incidents/{incident_id}/ai-investigation", response_model=AIInvestigationExecution)
def get_incident_ai_investigation(
    incident_id: str, session: DatabaseSession
) -> AIInvestigationExecution:
    incident = _incident_or_404(incident_id, session)
    service = AIInvestigationService(
        repository=InvestigationRepository(session),
        tools=_investigation_tools(),
        gateway=None,
        max_response_iterations=settings.ai_max_response_iterations,
        max_tool_calls=settings.ai_max_tool_calls,
        max_identical_tool_calls=settings.ai_max_identical_tool_calls,
    )
    execution = service.get_latest(incident.id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ai_investigation_not_found", "message": "AI investigation not found"},
        )
    return execution


@router.post(
    "/incidents/{incident_id}/investigate-agent-sdk",
    response_model=AIInvestigationExecution,
)
def investigate_incident_with_agents_sdk(
    incident_id: str,
    session: DatabaseSession,
    model: AgentsSDKModelDependency,
    tracing: TraceBoundaryDependency,
) -> AIInvestigationExecution:
    incident = _incident_or_404(incident_id, session)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "ai_not_configured", "message": "OpenAI is not configured"},
        )
    service = _agents_sdk_service(session, model, tracing)
    with tracing.span(
        "api.investigation.request",
        {
            "supportops.incident_reference": incident.catalog_id or incident_id,
            "supportops.runtime": "agents_sdk",
        },
    ):
        try:
            return service.investigate(incident)
        except ActiveInvestigationExistsError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "investigation_already_running", "message": str(error)},
            ) from error
        except AIInvestigationError as error:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"code": error.code, "message": str(error)},
            ) from error


@router.get(
    "/incidents/{incident_id}/agent-sdk-investigation",
    response_model=AIInvestigationExecution,
)
def get_incident_agents_sdk_investigation(
    incident_id: str, session: DatabaseSession
) -> AIInvestigationExecution:
    incident = _incident_or_404(incident_id, session)
    execution = _agents_sdk_service(session, None).get_latest(incident.id)
    if execution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "agents_sdk_investigation_not_found",
                "message": "Agents SDK investigation not found",
            },
        )
    return execution


@router.get(
    "/incidents/{incident_id}/investigations/{runtime}/events",
    response_model=list[InvestigationEventRead],
)
def get_investigation_events(
    incident_id: str,
    runtime: InvestigationRuntime,
    session: DatabaseSession,
    tracing: TraceBoundaryDependency,
) -> list[InvestigationEventRead]:
    incident = _incident_or_404(incident_id, session)
    mode = "ai" if runtime == InvestigationRuntime.MANUAL_RESPONSES else "agents_sdk"
    repository = InvestigationRepository(session, tracing)
    investigation = repository.get_ai_run(incident.id, mode=mode)
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ai_investigation_not_found",
                "message": "AI investigation not found",
            },
        )
    return [
        InvestigationEventRead.model_validate(event)
        for event in repository.list_events(investigation.id)
    ]


@router.get(
    "/incidents/{incident_id}/investigation-runs",
    response_model=list[AIInvestigationRead],
)
def list_investigation_runs(
    incident_id: str,
    session: DatabaseSession,
    runtime: InvestigationRuntime | None = None,
) -> list[AIInvestigationRead]:
    incident = _incident_or_404(incident_id, session)
    mode = _mode_for_runtime(runtime) if runtime is not None else None
    return [
        AIInvestigationRead.model_validate(run)
        for run in InvestigationRepository(session).list_ai_runs(incident.id, mode)
    ]


@router.get(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}",
    response_model=AIInvestigationRead,
)
def get_investigation_run(
    incident_id: str, investigation_id: int, session: DatabaseSession
) -> AIInvestigationRead:
    incident = _incident_or_404(incident_id, session)
    run = InvestigationRepository(session).get_ai_run_by_id(
        incident.id, investigation_id
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "investigation_run_not_found",
                "message": "Investigation run not found",
            },
        )
    return AIInvestigationRead.model_validate(run)


@router.get(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/events",
    response_model=list[InvestigationEventRead],
)
def get_historical_investigation_events(
    incident_id: str, investigation_id: int, session: DatabaseSession
) -> list[InvestigationEventRead]:
    incident = _incident_or_404(incident_id, session)
    repository = InvestigationRepository(session)
    run = repository.get_ai_run_by_id(incident.id, investigation_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "investigation_run_not_found",
                "message": "Investigation run not found",
            },
        )
    return [
        InvestigationEventRead.model_validate(event)
        for event in repository.list_events(run.id)
    ]


@router.get(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/artifacts",
    response_model=AIInvestigationExecution,
)
def get_historical_investigation_artifacts(
    incident_id: str, investigation_id: int, session: DatabaseSession
) -> AIInvestigationExecution:
    incident = _incident_or_404(incident_id, session)
    repository = InvestigationRepository(session)
    run = repository.get_ai_run_by_id(incident.id, investigation_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "investigation_run_not_found",
                "message": "Investigation run not found",
            },
        )
    origin = (
        InvestigationOrigin.AGENTS_SDK
        if run.mode == "agents_sdk"
        else InvestigationOrigin.AI
    )
    return AIInvestigationExecution(
        investigation=AIInvestigationRead.model_validate(run),
        evidence=[
            EvidenceRead.model_validate(item)
            for item in repository.list_evidence(incident.id, origin, run.id)
        ],
        steps=[
            InvestigationStepRead.model_validate(item)
            for item in repository.list_steps(incident.id, origin, run.id)
        ],
    )


@router.get(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals",
    response_model=list[ActionProposalRead],
)
def list_action_proposals(
    incident_id: str, investigation_id: int, session: DatabaseSession
) -> list[ActionProposalRead]:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    return _action_proposal_service(session).list(investigation)


@router.post(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals",
    response_model=ActionProposalRead,
    status_code=status.HTTP_201_CREATED,
)
def create_action_proposal(
    incident_id: str,
    investigation_id: int,
    payload: ActionProposalCreate,
    session: DatabaseSession,
) -> ActionProposalRead:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    try:
        return _action_proposal_service(session).create(investigation, payload)
    except InvalidActionTypeError as error:
        raise _proposal_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_action_type", error)
    except InvalidActionProposalError as error:
        raise _proposal_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_action_parameters", error)
    except ActionProposalEvidenceError as error:
        raise _proposal_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "evidence_mismatch", error)
    except ActionProposalEligibilityError as error:
        raise _proposal_error(status.HTTP_409_CONFLICT, "investigation_not_eligible", error)


@router.post(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals/{proposal_id}/approve",
    response_model=ActionProposalRead,
)
def approve_action_proposal(
    incident_id: str,
    investigation_id: int,
    proposal_id: int,
    session: DatabaseSession,
) -> ActionProposalRead:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    try:
        return _action_proposal_service(session).approve(investigation, proposal_id)
    except ActionProposalNotFoundError as error:
        raise _proposal_error(status.HTTP_404_NOT_FOUND, "action_proposal_not_found", error)
    except InvalidApprovalTransitionError as error:
        raise _proposal_error(status.HTTP_409_CONFLICT, "proposal_already_decided", error)


@router.post(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals/{proposal_id}/reject",
    response_model=ActionProposalRead,
)
def reject_action_proposal(
    incident_id: str,
    investigation_id: int,
    proposal_id: int,
    payload: ActionRejection,
    session: DatabaseSession,
) -> ActionProposalRead:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    try:
        return _action_proposal_service(session).reject(
            investigation, proposal_id, payload.reason
        )
    except ActionProposalNotFoundError as error:
        raise _proposal_error(status.HTTP_404_NOT_FOUND, "action_proposal_not_found", error)
    except InvalidApprovalTransitionError as error:
        raise _proposal_error(status.HTTP_409_CONFLICT, "proposal_already_decided", error)


@router.post(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals/{proposal_id}/execute",
    response_model=ActionExecutionRead,
)
def execute_action_proposal(
    incident_id: str,
    investigation_id: int,
    proposal_id: int,
    session: DatabaseSession,
    tools: ControlledToolsDependency,
) -> ActionExecutionRead:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    runtime = (
        InvestigationRuntime.AGENTS_SDK
        if investigation.mode == "agents_sdk"
        else InvestigationRuntime.MANUAL_RESPONSES
    )
    service = ActionExecutionService(
        ActionExecutionRepository(session),
        ExecutionPolicy(),
        tools,
    )
    try:
        return service.execute(
            investigation.incident_id, investigation.id, proposal_id, runtime
        )
    except ActionExecutionNotFoundError as error:
        raise _proposal_error(
            status.HTTP_404_NOT_FOUND, "action_proposal_not_found", error
        )
    except ActionExecutionNotApprovedError as error:
        raise _proposal_error(
            status.HTTP_409_CONFLICT, "proposal_not_approved", error
        )
    except ExecutionPolicyDeniedError as error:
        raise _proposal_error(
            status.HTTP_403_FORBIDDEN, "execution_policy_denied", error
        )


@router.get(
    "/incidents/{incident_id}/investigation-runs/{investigation_id}/action-proposals/{proposal_id}/execution",
    response_model=ActionExecutionRead,
)
def get_action_proposal_execution(
    incident_id: str,
    investigation_id: int,
    proposal_id: int,
    session: DatabaseSession,
) -> ActionExecutionRead:
    investigation = _investigation_or_404(incident_id, investigation_id, session)
    try:
        return ActionExecutionQueryService(
            ActionExecutionRepository(session)
        ).get_for_proposal(
            investigation.incident_id, investigation.id, proposal_id
        )
    except ActionExecutionNotFoundError as error:
        raise _proposal_error(
            status.HTTP_404_NOT_FOUND, "action_execution_not_found", error
        )


@router.post(
    "/action-executions/{execution_id}/attempts/{attempt_id}/stale-assessment",
    response_model=ActionExecutionStaleAssessmentRead,
)
def assess_stale_action_execution_attempt(
    execution_id: int,
    attempt_id: int,
    session: DatabaseSession,
) -> ActionExecutionStaleAssessmentRead:
    service = ActionExecutionRecoveryService(
        ActionExecutionRepository(session),
        settings.action_execution_attempt_stale_after_seconds,
    )
    try:
        return service.assess_stale_attempt(execution_id, attempt_id)
    except ActionExecutionRecoveryError as error:
        if error.code in {
            "action_execution_not_found",
            "execution_attempt_not_found",
        }:
            raise _proposal_error(status.HTTP_404_NOT_FOUND, error.code, error)
        if error.code in {
            "execution_attempt_not_stale",
            "execution_attempt_mismatch",
            "execution_attempt_not_canonical",
            "execution_attempt_already_terminal",
            "execution_recovery_conflict",
        }:
            raise _proposal_error(status.HTTP_409_CONFLICT, error.code, error)
        raise


@router.post(
    "/action-executions/{execution_id}/attempts/{attempt_id}/reconcile",
    response_model=ActionExecutionReconciliationRead,
)
def reconcile_action_execution_attempt(
    execution_id: int,
    attempt_id: int,
    session: DatabaseSession,
    tools: ControlledToolsDependency,
) -> ActionExecutionReconciliationRead:
    service = ActionExecutionReconciliationService(
        ActionExecutionReconciliationRepository(session),
        VerificationPolicy(),
        tools,
    )
    try:
        return service.reconcile(execution_id, attempt_id)
    except ActionExecutionReconciliationError as error:
        if error.code in {
            "action_execution_not_found",
            "execution_attempt_not_found",
        }:
            raise _proposal_error(status.HTTP_404_NOT_FOUND, error.code, error)
        raise _proposal_error(status.HTTP_409_CONFLICT, error.code, error)
    except VerificationPolicyDeniedError as error:
        raise _proposal_error(
            status.HTTP_403_FORBIDDEN,
            "reconciliation_policy_denied",
            error,
        )


@router.post(
    "/action-executions/{execution_id}/attempts/{attempt_id}/reconciliation/recover",
    response_model=ActionExecutionReconciliationRead,
)
def recover_action_execution_reconciliation(
    execution_id: int,
    attempt_id: int,
    session: DatabaseSession,
    tools: ControlledToolsDependency,
) -> ActionExecutionReconciliationRead:
    service = ActionExecutionReconciliationService(
        ActionExecutionReconciliationRepository(session),
        VerificationPolicy(),
        tools,
        settings.action_execution_reconciliation_stale_after_seconds,
    )
    try:
        return service.recover(execution_id, attempt_id)
    except ActionExecutionReconciliationError as error:
        if error.code in {
            "action_execution_not_found",
            "execution_attempt_not_found",
            "action_execution_reconciliation_not_found",
        }:
            raise _proposal_error(status.HTTP_404_NOT_FOUND, error.code, error)
        raise _proposal_error(status.HTTP_409_CONFLICT, error.code, error)
    except VerificationPolicyDeniedError as error:
        raise _proposal_error(
            status.HTTP_403_FORBIDDEN,
            "reconciliation_policy_denied",
            error,
        )


@router.get(
    "/action-executions/{execution_id}/attempts/{attempt_id}/reconciliation",
    response_model=ActionExecutionReconciliationOperationalRead,
)
def get_action_execution_reconciliation(
    execution_id: int,
    attempt_id: int,
    session: DatabaseSession,
) -> ActionExecutionReconciliationOperationalRead:
    service = ActionExecutionReconciliationService(
        ActionExecutionReconciliationRepository(session),
        VerificationPolicy(),
        reconciliation_stale_after_seconds=(
            settings.action_execution_reconciliation_stale_after_seconds
        ),
    )
    try:
        return service.get_operational_view(execution_id, attempt_id)
    except ActionExecutionReconciliationError as error:
        if error.code in {
            "action_execution_not_found",
            "execution_attempt_not_found",
            "action_execution_reconciliation_not_found",
        }:
            raise _proposal_error(status.HTTP_404_NOT_FOUND, error.code, error)
        raise _proposal_error(status.HTTP_409_CONFLICT, error.code, error)


@router.post(
    "/action-executions/{execution_id}/verify",
    response_model=OutcomeVerificationRead,
)
def verify_action_execution(
    execution_id: int,
    session: DatabaseSession,
    tools: ControlledToolsDependency,
) -> OutcomeVerificationRead:
    service = OutcomeVerificationService(
        OutcomeVerificationRepository(session), VerificationPolicy(), tools
    )
    try:
        return service.verify(execution_id)
    except VerificationExecutionNotFoundError as error:
        raise _proposal_error(
            status.HTTP_404_NOT_FOUND, "action_execution_not_found", error
        )
    except ActionExecutionNotCompletedError as error:
        raise _proposal_error(
            status.HTTP_409_CONFLICT, "execution_not_completed", error
        )
    except VerificationPolicyDeniedError as error:
        raise _proposal_error(
            status.HTTP_403_FORBIDDEN, "verification_policy_denied", error
        )


@router.get(
    "/action-executions/{execution_id}/verification",
    response_model=OutcomeVerificationRead,
)
def get_action_execution_verification(
    execution_id: int,
    session: DatabaseSession,
    tools: ControlledToolsDependency,
) -> OutcomeVerificationRead:
    service = OutcomeVerificationService(
        OutcomeVerificationRepository(session), VerificationPolicy(), tools
    )
    try:
        return service.get(execution_id)
    except OutcomeVerificationNotFoundError as error:
        raise _proposal_error(
            status.HTTP_404_NOT_FOUND, "outcome_verification_not_found", error
        )


@router.post(
    "/incidents/{incident_id}/resolution-decisions",
    response_model=IncidentResolutionRead,
)
def decide_incident_resolution(
    incident_id: str,
    payload: IncidentResolutionCreate,
    session: DatabaseSession,
) -> IncidentResolutionRead:
    incident = _incident_or_404(incident_id, session)
    service = IncidentResolutionService(IncidentResolutionRepository(session))
    try:
        return service.decide(incident.id, payload)
    except ResolutionVerificationNotFoundError as error:
        raise _proposal_error(
            status.HTTP_404_NOT_FOUND, "outcome_verification_not_found", error
        )
    except ResolutionOwnershipError as error:
        raise _proposal_error(
            status.HTTP_409_CONFLICT, "verification_incident_mismatch", error
        )
    except ResolutionNotEligibleError as error:
        raise _proposal_error(
            status.HTTP_409_CONFLICT, "resolution_not_eligible", error
        )
    except ResolutionDecisionConflictError as error:
        raise _proposal_error(
            status.HTTP_409_CONFLICT, "resolution_decision_conflict", error
        )
    except ResolutionIncidentNotFoundError as error:
        raise _proposal_error(status.HTTP_404_NOT_FOUND, "incident_not_found", error)


@router.get(
    "/incidents/{incident_id}/resolution-decisions",
    response_model=list[IncidentResolutionRead],
)
def list_incident_resolution_decisions(
    incident_id: str, session: DatabaseSession
) -> list[IncidentResolutionRead]:
    incident = _incident_or_404(incident_id, session)
    return IncidentResolutionService(
        IncidentResolutionRepository(session)
    ).list(incident.id)


def _mode_for_runtime(runtime: InvestigationRuntime) -> str:
    return "ai" if runtime == InvestigationRuntime.MANUAL_RESPONSES else "agents_sdk"


def _action_proposal_service(session: Session) -> ActionProposalService:
    return ActionProposalService(
        ActionProposalRepository(session), InvestigationRepository(session)
    )


def _investigation_or_404(
    incident_id: str, investigation_id: int, session: Session
):
    incident = _incident_or_404(incident_id, session)
    investigation = InvestigationRepository(session).get_ai_run_by_id(
        incident.id, investigation_id
    )
    if investigation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "investigation_run_not_found",
                "message": "Investigation run not found",
            },
        )
    return investigation


def _proposal_error(status_code: int, code: str, error: Exception) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(error)},
    )


def _agents_sdk_service(
    session: Session, model: Model | None, tracing: TraceBoundary | None = None
) -> AgentsSDKInvestigationService:
    tracing = tracing or TraceBoundary()
    return AgentsSDKInvestigationService(
        repository=InvestigationRepository(session, tracing),
        tools=_investigation_tools(),
        model=model,
        model_name=settings.openai_model,
        max_turns=settings.ai_max_response_iterations,
        max_tool_calls=settings.ai_max_tool_calls,
        max_identical_tool_calls=settings.ai_max_identical_tool_calls,
        max_output_tokens=settings.ai_max_output_tokens,
        timeout_seconds=settings.openai_timeout_seconds,
        tracing=tracing,
    )


def _investigation_tools() -> InvestigationToolRegistry:
    return build_investigation_tools(settings)


def _incident_or_404(incident_id: str, session: Session) -> IncidentRecord:
    incident = IncidentRepository(session).get_by_reference(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "incident_not_found", "message": "Incident not found"},
        )
    return incident
