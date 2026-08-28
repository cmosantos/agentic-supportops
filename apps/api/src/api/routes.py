from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from agents import Model

from api.dependencies import get_agents_sdk_model, get_db_session, get_responses_gateway, get_trace_boundary
from core.config import settings
from domain.ai import AIInvestigationExecution, AIInvestigationRead, InvestigationEventRead, InvestigationRuntime
from db.models import IncidentRecord
from domain.incident import IncidentCreate, IncidentRead
from domain.investigation import EvidenceRead, InvestigationRead
from repositories.incident_repository import IncidentRepository
from repositories.investigation_repository import ActiveInvestigationExistsError, InvestigationRepository
from services.incident_service import IncidentService
from integrations.responses_gateway import ResponsesGateway
from integrations.mcp_client import build_investigation_tools
from services.ai_investigation_service import AIInvestigationError, AIInvestigationService
from services.agents_sdk_investigation_service import AgentsSDKInvestigationService
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


def _mode_for_runtime(runtime: InvestigationRuntime) -> str:
    return "ai" if runtime == InvestigationRuntime.MANUAL_RESPONSES else "agents_sdk"


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
