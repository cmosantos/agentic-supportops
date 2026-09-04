import { useEffect, useRef, useState } from "react";

type Health = { status: string; service: string };
type Incident = {
  id: number;
  catalog_id: string | null;
  title: string;
  description: string;
  category: string;
  priority: "low" | "medium" | "high" | "critical";
  status: "open" | "investigating" | "awaiting_approval" | "resolved" | "closed";
  requester: string;
  affected_resource_type: string | null;
  affected_resource_id: string | null;
  investigation_context: Record<string, string>;
  created_at: string;
  updated_at: string;
};
type InvestigationOrigin = "deterministic" | "ai" | "agents_sdk";
type Evidence = {
  id: number;
  incident_id: number;
  investigation_id: number | null;
  source: string;
  resource: string;
  origin: InvestigationOrigin;
  payload: Record<string, unknown>;
  created_at: string;
};
type InvestigationStep = {
  id: number;
  incident_id: number;
  investigation_id: number | null;
  tool: string;
  target_resource: string;
  origin: InvestigationOrigin;
  arguments: Record<string, unknown>;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  result: Record<string, unknown>;
  created_at: string;
  completed_at: string | null;
};
type AIStatus = "running" | "completed" | "insufficient_evidence" | "failed";
type AIResult = {
  status: AIStatus;
  summary: string;
  diagnosis: string;
  confidence: number;
  supporting_evidence: string[];
  evidence_ids: number[];
  recommended_next_steps: string[];
  missing_information: string[];
  human_action_required: boolean;
  proposed_action: ActionProposalInput | null;
};
type ActionProposalInput = {
  action_type: string;
  target: string;
  parameters: Record<string, unknown>;
  rationale: string;
  supporting_evidence_ids: number[];
  risk_level: "low" | "medium" | "high";
};
type ActionProposal = ActionProposalInput & {
  id: number;
  investigation_id: number;
  incident_id: number;
  approval_status: "pending" | "approved" | "rejected";
  created_at: string;
  decision_at: string | null;
  rejection_reason: string | null;
};
type ActionExecution = {
  id: number;
  proposal_id: number;
  incident_id: number;
  capability_name: string;
  status: "running" | "completed" | "failed" | "outcome_unknown";
  requested_at: string;
  started_at: string;
  completed_at: string | null;
  result: {
    data?: {
      target?: string;
      previous_state?: string;
      current_state?: string;
      restarted?: boolean;
    };
  } | null;
  error: { code: string; message: string } | null;
  completion_basis: "acknowledged_result" | "reconciliation" | "legacy_recorded" | null;
};
type ActionExecutionAttempt = {
  id: number;
  attempt_number: number;
  status: "running" | "completed" | "failed" | "outcome_unknown";
  invocation_started_at: string | null;
  outcome_certainty: "applied_acknowledged" | "not_applied" | "unknown" | "legacy_undetermined" | null;
};
type InvestigationEvent = {
  id: number;
  investigation_id: number;
  runtime: "manual_responses" | "agents_sdk";
  event_type: string;
  sequence: number;
  status: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
};
type InvestigationRun = AIExecution["investigation"];
type Reconciliation = {
  id: number;
  attempt_id: number;
  execution_id: number;
  status: "running" | "desired_state_observed" | "undesired_state_observed" | "inconclusive";
  observer: string;
  expected_outcome: { state?: string };
  observed_outcome: { state?: string } | null;
  error: { code: string; message: string } | null;
  requested_at: string;
  started_at: string;
  completed_at: string | null;
  is_stale?: boolean;
  recoverable?: boolean;
  recovery_block_reason?: string | null;
};
type OutcomeVerification = {
  id: number;
  execution_id: number;
  proposal_id: number;
  incident_id: number;
  status: "running" | "verified" | "not_verified" | "failed";
  requested_at: string;
  started_at: string;
  completed_at: string | null;
  expected_outcome: { state?: string };
  observed_outcome: { state?: string } | null;
  evidence: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
};
type IncidentResolutionDecision = {
  id: number;
  incident_id: number;
  verification_id: number;
  execution_id: number;
  proposal_id: number;
  decision: "resolve" | "keep_open";
  reason: string | null;
  decided_at: string;
};
type ActionExecutionTimelineEntry = {
  timestamp: string;
  event_type: string;
  execution_id: number;
  attempt_id: number | null;
  status: string | null;
  description: string;
  reason: string | null;
};
type Investigation = {
  incident_id: number;
  catalog_id: string | null;
  steps: InvestigationStep[];
  evidence: Evidence[];
};
type AIExecution = {
  investigation: {
    id: number;
    incident_id: number;
    mode: string;
    status: AIStatus;
    model: string;
    response_id: string | null;
    result: AIResult | null;
    usage: {
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      response_iterations: number;
    };
    error: Record<string, unknown> | null;
    created_at: string;
    completed_at: string | null;
  };
  evidence: Evidence[];
  steps: InvestigationStep[];
};
type InvestigationMode = "deterministic" | "ai" | "agents_sdk";
type AIMetadata = { status: AIStatus; model: string };
type ExecutionLookupStatus = "idle" | "loading" | "not_found" | "found" | "error";
type ReconciliationLookupStatus = "idle" | "loading" | "not_found" | "found" | "error";
type TimelineLookupStatus = "idle" | "loading" | "loaded" | "error";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

async function investigationErrorMessage(response: Response): Promise<string> {
  const fallback = `Investigation failed (${response.status})`;
  try {
    const body: unknown = await response.json();
    if (typeof body === "string" && body.trim()) return body;
    if (!isRecord(body)) return fallback;
    const detail = body.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (isRecord(detail) && typeof detail.message === "string" && detail.message.trim()) {
      return detail.message;
    }
    if (typeof body.message === "string" && body.message.trim()) return body.message;
  } catch {
    // The status-based fallback remains useful for non-JSON responses.
  }
  return fallback;
}

function displayStatus(status: string): string {
  return status.replaceAll("_", " ");
}

function isReconciliationEligible(attempt: ActionExecutionAttempt | null): boolean {
  return Boolean(
    attempt &&
    attempt.attempt_number === 1 &&
    attempt.status === "outcome_unknown" &&
    attempt.invocation_started_at &&
    attempt.outcome_certainty === "unknown"
  );
}

function displayAction(action: string): string {
  const labels: Record<string, string> = {
    restart_simulated_service: "Restart simulated service",
    unlock_simulated_user: "Unlock simulated user",
    reset_simulated_application_state: "Reset simulated application state",
  };
  return labels[action] ?? displayStatus(action);
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function toneFor(status: string): string {
  if (["completed", "approved", "verified", "resolved", "desired_state_observed", "applied_acknowledged"].includes(status)) return "success";
  if (["running", "investigating", "pending", "awaiting_approval"].includes(status)) return "progress";
  if (["outcome_unknown", "unknown", "not_verified", "inconclusive", "keep_open"].includes(status)) return "warning";
  if (["failed", "rejected", "undesired_state_observed"].includes(status)) return "danger";
  return "neutral";
}

function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge ${toneFor(status)}`}>{displayStatus(status).toUpperCase()}</span>;
}

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [steps, setSteps] = useState<InvestigationStep[]>([]);
  const [investigating, setInvestigating] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [mode, setMode] = useState<InvestigationMode | null>(null);
  const [aiConfigured, setAiConfigured] = useState(false);
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [aiMetadata, setAiMetadata] = useState<AIMetadata | null>(null);
  const [investigationRuns, setInvestigationRuns] = useState<InvestigationRun[]>([]);
  const [events, setEvents] = useState<InvestigationEvent[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionProposal, setActionProposal] = useState<ActionProposal | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [decidingProposal, setDecidingProposal] = useState(false);
  const [actionExecution, setActionExecution] = useState<ActionExecution | null>(null);
  const [executionLookupStatus, setExecutionLookupStatus] = useState<ExecutionLookupStatus>("idle");
  const [executingAction, setExecutingAction] = useState(false);
  const [actionExecutionAttempt, setActionExecutionAttempt] = useState<ActionExecutionAttempt | null>(null);
  const [reconciliation, setReconciliation] = useState<Reconciliation | null>(null);
  const [reconciliationLookupStatus, setReconciliationLookupStatus] = useState<ReconciliationLookupStatus>("idle");
  const [reconciling, setReconciling] = useState(false);
  const [outcomeVerification, setOutcomeVerification] = useState<OutcomeVerification | null>(null);
  const [verifyingOutcome, setVerifyingOutcome] = useState(false);
  const [resolutionDecisions, setResolutionDecisions] = useState<IncidentResolutionDecision[]>([]);
  const [resolutionReason, setResolutionReason] = useState("");
  const [decidingResolution, setDecidingResolution] = useState(false);
  const [executionTimeline, setExecutionTimeline] = useState<ActionExecutionTimelineEntry[]>([]);
  const [timelineLookupStatus, setTimelineLookupStatus] = useState<TimelineLookupStatus>("idle");
  const investigationRequest = useRef<AbortController | null>(null);
  const investigationVersion = useRef(0);
  const proposalDecisionInFlight = useRef(false);
  const executionRequestInFlight = useRef(false);
  const reconciliationRequestInFlight = useRef(false);

  async function loadExecutionTimeline(
    executionId: number,
    signal?: AbortSignal,
    requestVersion?: number,
  ) {
    const isCurrent = () => requestVersion === undefined || requestVersion === investigationVersion.current;
    setTimelineLookupStatus("loading");
    try {
      const response = await fetch(
        `${apiBaseUrl}/action-executions/${executionId}/timeline`,
        { signal },
      );
      if (!isCurrent()) return;
      if (!response.ok) {
        setExecutionTimeline([]);
        setTimelineLookupStatus("error");
        return;
      }
      setExecutionTimeline(await response.json());
      setTimelineLookupStatus("loaded");
    } catch {
      if (signal?.aborted || !isCurrent()) return;
      setExecutionTimeline([]);
      setTimelineLookupStatus("error");
    }
  }

  async function loadReconciliationContext(
    execution: ActionExecution,
    signal?: AbortSignal,
    requestVersion?: number,
  ) {
    const isCurrent = () => requestVersion === undefined || requestVersion === investigationVersion.current;
    setActionExecutionAttempt(null);
    setReconciliation(null);
    setReconciliationLookupStatus("idle");
    if (
      execution.status !== "outcome_unknown" &&
      execution.completion_basis !== "reconciliation"
    ) return;
    setReconciliationLookupStatus("loading");
    try {
      const attemptResponse = await fetch(`${apiBaseUrl}/action-executions/${execution.id}/attempt`, { signal });
      if (!isCurrent()) return;
      if (!attemptResponse.ok) {
        setReconciliationLookupStatus("error");
        setProposalError(await investigationErrorMessage(attemptResponse));
        return;
      }
      const attempt: ActionExecutionAttempt = await attemptResponse.json();
      setActionExecutionAttempt(attempt);
      const reconciliationResponse = await fetch(
        `${apiBaseUrl}/action-executions/${execution.id}/attempts/${attempt.id}/reconciliation`,
        { signal },
      );
      if (!isCurrent()) return;
      if (reconciliationResponse.status === 404) {
        setReconciliationLookupStatus("not_found");
        return;
      }
      if (!reconciliationResponse.ok) {
        setReconciliationLookupStatus("error");
        setProposalError(await investigationErrorMessage(reconciliationResponse));
        return;
      }
      setReconciliation(await reconciliationResponse.json());
      setReconciliationLookupStatus("found");
    } catch {
      if (signal?.aborted || !isCurrent()) return;
      setReconciliationLookupStatus("error");
      setProposalError("Unable to load persisted reconciliation state.");
    }
  }

  useEffect(() => {
    const controller = new AbortController();

    async function loadCoreApplication() {
      try {
        const [healthResponse, incidentResponse] = await Promise.all([
          fetch(`${apiBaseUrl}/health`, { signal: controller.signal }),
          fetch(`${apiBaseUrl}/incidents`, { signal: controller.signal }),
        ]);
        if (!healthResponse.ok || !incidentResponse.ok) {
          throw new Error("Core application data failed");
        }
        const healthResult: Health = await healthResponse.json();
        const incidentResult: Incident[] = await incidentResponse.json();
        setHealth(healthResult);
        setIncidents(incidentResult);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      }
    }

    async function loadAIConfiguration() {
      try {
        const response = await fetch(`${apiBaseUrl}/ai/config`, {
          signal: controller.signal,
        });
        if (!response.ok) return;
        const config: { configured: boolean } = await response.json();
        setAiConfigured(config.configured);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAiConfigured(false);
      }
    }

    void loadCoreApplication();
    void loadAIConfiguration();
    return () => {
      controller.abort();
      investigationRequest.current?.abort();
      investigationVersion.current += 1;
    };
  }, []);

  function selectIncident(incident: Incident) {
    investigationRequest.current?.abort();
    investigationRequest.current = null;
    investigationVersion.current += 1;
    setSelected(incident);
    setEvidence([]);
    setSteps([]);
    setAiResult(null);
    setAiMetadata(null);
    setInvestigationRuns([]);
    setEvents([]);
    setHistoryLoading(true);
    setActionProposal(null);
    setProposalError(null);
    setActionExecution(null);
    setExecutionLookupStatus("idle");
    setExecutingAction(false);
    setActionExecutionAttempt(null);
    setReconciliation(null);
    setReconciliationLookupStatus("idle");
    setReconciling(false);
    setOutcomeVerification(null);
    setVerifyingOutcome(false);
    setResolutionDecisions([]);
    setResolutionReason("");
    setDecidingResolution(false);
    setExecutionTimeline([]);
    setTimelineLookupStatus("idle");
    setMode(null);
    setInvestigationError(null);
    setInvestigating(false);
    const selectionVersion = investigationVersion.current;
    const reference = incident.catalog_id ?? incident.id;
    void fetch(`${apiBaseUrl}/incidents/${reference}/resolution-decisions`)
      .then(async (response) => response.ok ? response.json() : [])
      .then((decisions: IncidentResolutionDecision[]) => {
        if (selectionVersion === investigationVersion.current) {
          setResolutionDecisions(decisions);
        }
      })
      .catch(() => undefined);
    void fetch(`${apiBaseUrl}/incidents/${reference}/investigation-runs`)
      .then(async (response) => response.ok ? response.json() : [])
      .then((runs: InvestigationRun[]) => {
        if (selectionVersion === investigationVersion.current) {
          setInvestigationRuns(runs);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (selectionVersion === investigationVersion.current) setHistoryLoading(false);
      });
  }

  async function loadRun(run: InvestigationRun) {
    if (!selected) return;
    const version = investigationVersion.current;
    const reference = selected.catalog_id ?? selected.id;
    setHistoryLoading(true);
    setProposalError(null);
    try {
      const [artifactsResponse, eventsResponse, proposalsResponse] = await Promise.all([
        fetch(`${apiBaseUrl}/incidents/${reference}/investigation-runs/${run.id}/artifacts`),
        fetch(`${apiBaseUrl}/incidents/${reference}/investigation-runs/${run.id}/events`),
        fetch(`${apiBaseUrl}/incidents/${reference}/investigation-runs/${run.id}/action-proposals`),
      ]);
      if (!artifactsResponse.ok || !eventsResponse.ok || !proposalsResponse.ok) {
        throw new Error("Historical investigation details could not be loaded");
      }
      const artifacts: AIExecution = await artifactsResponse.json();
      const historicalEvents: InvestigationEvent[] = await eventsResponse.json();
      const proposals: ActionProposal[] = await proposalsResponse.json();
      if (version !== investigationVersion.current) return;
      setEvidence(artifacts.evidence);
      setSteps(artifacts.steps);
      setAiResult(artifacts.investigation.result);
      setAiMetadata({ status: artifacts.investigation.status, model: artifacts.investigation.model });
      setMode(artifacts.investigation.mode === "agents_sdk" ? "agents_sdk" : "ai");
      setEvents(historicalEvents);
      setActionProposal(proposals.at(-1) ?? null);
      setActionExecution(null);
      setExecutionLookupStatus("idle");
      setActionExecutionAttempt(null);
      setReconciliation(null);
      setReconciliationLookupStatus("idle");
      setReconciling(false);
      setOutcomeVerification(null);
      setExecutionTimeline([]);
      setTimelineLookupStatus("idle");
    } catch (error: unknown) {
      if (version === investigationVersion.current) {
        setProposalError(error instanceof Error ? error.message : "History loading failed");
      }
    } finally {
      if (version === investigationVersion.current) setHistoryLoading(false);
    }
  }

  async function runInvestigation(investigationMode: InvestigationMode) {
    if (!selected || investigationRequest.current) return;

    const controller = new AbortController();
    const requestVersion = ++investigationVersion.current;
    investigationRequest.current = controller;
    setInvestigating(true);
    setInvestigationError(null);
    setEvidence([]);
    setSteps([]);
    setAiResult(null);
    setAiMetadata(null);
    setActionProposal(null);
    setProposalError(null);
    setActionExecution(null);
    setExecutionLookupStatus("idle");
    setExecutingAction(false);
    setActionExecutionAttempt(null);
    setReconciliation(null);
    setReconciliationLookupStatus("idle");
    setReconciling(false);
    setOutcomeVerification(null);
    setVerifyingOutcome(false);
    setResolutionDecisions([]);
    setResolutionReason("");
    setDecidingResolution(false);
    setExecutionTimeline([]);
    setTimelineLookupStatus("idle");
    setMode(investigationMode);

    async function loadProposalAndExecution(proposal: ActionProposal, reference: string | number) {
      if (requestVersion !== investigationVersion.current) return;
      setActionProposal(proposal);
      setActionExecution(null);
      setExecutionLookupStatus("loading");
      const executionUrl = `${apiBaseUrl}/incidents/${reference}/investigation-runs/${proposal.investigation_id}/action-proposals/${proposal.id}/execution`;
      try {
        const executionResponse = await fetch(executionUrl, { signal: controller.signal });
        if (requestVersion !== investigationVersion.current) return;
        if (executionResponse.status === 404) {
          setExecutionLookupStatus("not_found");
          return;
        }
        if (!executionResponse.ok) {
          setExecutionLookupStatus("error");
          setProposalError(await investigationErrorMessage(executionResponse));
          return;
        }
        const execution: ActionExecution = await executionResponse.json();
        setActionExecution(execution);
        setExecutionLookupStatus("found");
        await Promise.all([
          loadReconciliationContext(execution, controller.signal, requestVersion),
          loadExecutionTimeline(execution.id, controller.signal, requestVersion),
        ]);
      } catch (error: unknown) {
        if (controller.signal.aborted || requestVersion !== investigationVersion.current) return;
        setExecutionLookupStatus("error");
        setProposalError("Unable to load persisted execution state.");
      }
    }

    try {
      const reference = selected.catalog_id ?? selected.id;
      const endpoint = investigationMode === "ai"
        ? "investigate-ai"
        : investigationMode === "agents_sdk"
          ? "investigate-agent-sdk"
          : "investigate";
      const response = await fetch(`${apiBaseUrl}/incidents/${reference}/${endpoint}`, {
        method: "POST",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      if (requestVersion !== investigationVersion.current) return;

      if (investigationMode !== "deterministic") {
        const result: AIExecution = await response.json();
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
        setSteps(result.steps);
        setAiResult(result.investigation.result);
        setAiMetadata({
          status: result.investigation.status,
          model: result.investigation.model,
        });
        setInvestigationRuns((current) => [
          result.investigation,
          ...current.filter((item) => item.id !== result.investigation.id),
        ]);
        void fetch(
          `${apiBaseUrl}/incidents/${reference}/investigation-runs/${result.investigation.id}/events`,
          { signal: controller.signal },
        )
          .then(async (eventResponse) => eventResponse.ok ? eventResponse.json() : [])
          .then((items: InvestigationEvent[]) => {
            if (requestVersion === investigationVersion.current) setEvents(items);
          })
          .catch(() => undefined);
        if (result.investigation.result?.proposed_action) {
          const proposalUrl = `${apiBaseUrl}/incidents/${reference}/investigation-runs/${result.investigation.id}/action-proposals`;
          const persistedResponse = await fetch(proposalUrl, { signal: controller.signal });
          if (requestVersion !== investigationVersion.current) return;
          if (!persistedResponse.ok) {
            setProposalError(await investigationErrorMessage(persistedResponse));
            return;
          }
          const persistedProposals: ActionProposal[] = await persistedResponse.json();
          if (persistedProposals.length > 0) {
            await loadProposalAndExecution(persistedProposals[0], reference);
            return;
          }
          const proposalResponse = await fetch(proposalUrl, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(result.investigation.result.proposed_action),
            signal: controller.signal,
          });
          if (requestVersion !== investigationVersion.current) return;
          if (!proposalResponse.ok) {
            setProposalError(await investigationErrorMessage(proposalResponse));
          } else if (requestVersion === investigationVersion.current) {
            await loadProposalAndExecution(await proposalResponse.json(), reference);
          }
        }
      } else {
        const result: Investigation = await response.json();
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
        setSteps(result.steps);
      }
    } catch (error: unknown) {
      if (controller.signal.aborted || requestVersion !== investigationVersion.current) return;
      setInvestigationError(
        error instanceof Error ? error.message : "Investigation failed",
      );
    } finally {
      if (requestVersion === investigationVersion.current) {
        investigationRequest.current = null;
        setInvestigating(false);
      }
    }
  }

  async function decideActionProposal(decision: "approve" | "reject") {
    if (!selected || !actionProposal || proposalDecisionInFlight.current) return;
    proposalDecisionInFlight.current = true;
    setDecidingProposal(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await fetch(
        `${apiBaseUrl}/incidents/${reference}/investigation-runs/${actionProposal.investigation_id}/action-proposals/${actionProposal.id}/${decision}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: decision === "reject"
            ? JSON.stringify({ reason: "Rejected by the human operator." })
            : undefined,
        },
      );
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      setActionProposal(await response.json());
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Proposal decision failed");
    } finally {
      proposalDecisionInFlight.current = false;
      setDecidingProposal(false);
    }
  }

  async function executeApprovedAction() {
    if (!selected || !actionProposal || executionRequestInFlight.current || actionExecution) return;
    executionRequestInFlight.current = true;
    setExecutingAction(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await fetch(
        `${apiBaseUrl}/incidents/${reference}/investigation-runs/${actionProposal.investigation_id}/action-proposals/${actionProposal.id}/execute`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      const execution: ActionExecution = await response.json();
      setActionExecution(execution);
      setExecutionLookupStatus("found");
      await Promise.all([
        loadReconciliationContext(execution),
        loadExecutionTimeline(execution.id),
      ]);
      if (execution.status === "completed") {
        try {
          const verificationResponse = await fetch(
            `${apiBaseUrl}/action-executions/${execution.id}/verification`,
          );
          if (verificationResponse.ok) {
            setOutcomeVerification(await verificationResponse.json());
          }
        } catch {
          // Absence of prior verification leaves the explicit action available.
        }
      }
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Action execution failed");
    } finally {
      executionRequestInFlight.current = false;
      setExecutingAction(false);
    }
  }

  async function reconcileExecution(recover = false) {
    if (
      !selected ||
      !actionProposal ||
      !actionExecution ||
      !actionExecutionAttempt ||
      reconciliationRequestInFlight.current
    ) return;
    reconciliationRequestInFlight.current = true;
    setReconciling(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await fetch(
        `${apiBaseUrl}/action-executions/${actionExecution.id}/attempts/${actionExecutionAttempt.id}/${recover ? "reconciliation/recover" : "reconcile"}`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      setReconciliation(await response.json());
      setReconciliationLookupStatus("found");
      const executionResponse = await fetch(
        `${apiBaseUrl}/incidents/${reference}/investigation-runs/${actionProposal.investigation_id}/action-proposals/${actionProposal.id}/execution`,
      );
      if (!executionResponse.ok) throw new Error(await investigationErrorMessage(executionResponse));
      const refreshedExecution: ActionExecution = await executionResponse.json();
      setActionExecution(refreshedExecution);
      await loadExecutionTimeline(refreshedExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Reconciliation failed");
    } finally {
      reconciliationRequestInFlight.current = false;
      setReconciling(false);
    }
  }

  async function verifyOutcome() {
    if (
      !actionExecution ||
      actionExecution.status !== "completed" ||
      verifyingOutcome ||
      outcomeVerification
    ) return;
    setVerifyingOutcome(true);
    setProposalError(null);
    try {
      const response = await fetch(
        `${apiBaseUrl}/action-executions/${actionExecution.id}/verify`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      setOutcomeVerification(await response.json());
      await loadExecutionTimeline(actionExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Outcome verification failed");
    } finally {
      setVerifyingOutcome(false);
    }
  }

  async function decideResolution(decision: "resolve" | "keep_open") {
    if (
      !selected ||
      !outcomeVerification ||
      decidingResolution ||
      resolutionDecisions.some((item) => item.verification_id === outcomeVerification.id)
    ) return;
    setDecidingResolution(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await fetch(
        `${apiBaseUrl}/incidents/${reference}/resolution-decisions`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            verification_id: outcomeVerification.id,
            decision,
            reason: resolutionReason.trim() || null,
          }),
        },
      );
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      const resolution: IncidentResolutionDecision = await response.json();
      setResolutionDecisions((current) =>
        current.some((item) => item.id === resolution.id) ? current : [...current, resolution]
      );
      if (resolution.decision === "resolve") {
        setSelected((current) => current ? { ...current, status: "resolved" } : current);
        setIncidents((current) => current.map((item) =>
          item.id === resolution.incident_id ? { ...item, status: "resolved" } : item
        ));
      }
      if (actionExecution) await loadExecutionTimeline(actionExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Resolution decision failed");
    } finally {
      setDecidingResolution(false);
    }
  }

  const outcomeCertainty = actionExecutionAttempt?.outcome_certainty ?? null;
  const currentResolution = outcomeVerification
    ? resolutionDecisions.find((item) => item.verification_id === outcomeVerification.id)
    : resolutionDecisions.at(-1);

  return (
    <main>
      <section className="shell">
        <header className="app-header">
          <div>
            <p className="eyebrow">Support operations console</p>
            <h1>Agentic SupportOps</h1>
            <p className="summary">Investigate incidents, govern remediation, and verify outcomes.</p>
          </div>
          <div className="system-status" aria-label="System status">
            <div className="health" aria-live="polite">
              <span className={health ? "indicator online" : "indicator"} />
              {health
                ? `Backend online — ${health.service}`
                : unavailable
                  ? "Backend unavailable"
                  : "Checking backend health…"}
            </div>
            <span className="system-chip">Transport · HTTP API</span>
            <span className={aiConfigured ? "system-chip available" : "system-chip"}>
              AI · {aiConfigured ? "available" : "not configured"}
            </span>
          </div>
        </header>
        <div className="workspace">
          <aside className="queue">
            <div className="section-heading">
              <div><p className="section-kicker">Active workload</p><h2>Incident queue</h2></div>
              <span className="count">{incidents.length}</span>
            </div>
            <div className="incident-list">
              {incidents.map((incident) => (
                <button
                  className={selected?.id === incident.id ? "incident selected" : "incident"}
                  key={incident.id}
                  onClick={() => selectIncident(incident)}
                >
                  <span className="incident-topline">
                    <strong>{incident.catalog_id ?? `#${incident.id}`}</strong>
                    <StatusBadge status={incident.priority} />
                  </span>
                  <span className="incident-title">{incident.title}</span>
                  <span className="incident-meta">{incident.category} · {displayStatus(incident.status)}</span>
                </button>
              ))}
            </div>
          </aside>
          <div className="details">
            {selected ? (
              <>
                <header className="incident-header">
                  <div>
                    <p className="section-kicker">{selected.catalog_id ?? `Incident #${selected.id}`}</p>
                    <h2>{selected.title}</h2>
                    <p className="incident-description">{selected.description}</p>
                  </div>
                  <StatusBadge status={selected.status} />
                </header>
                <dl className="incident-facts">
                  <div><dt>Severity</dt><dd><StatusBadge status={selected.priority} /></dd></div>
                  <div><dt>Category</dt><dd>{selected.category}</dd></div>
                  <div><dt>Affected resource</dt><dd>{selected.affected_resource_id ?? "Not specified"}</dd></div>
                  <div><dt>Updated</dt><dd>{formatTime(selected.updated_at)}</dd></div>
                </dl>
                <p className="sr-only"><b>Incident status:</b> {selected.status.toUpperCase()}</p>
                <section className="lifecycle" aria-label="Operational lifecycle">
                  {[
                    ["Investigation", aiMetadata?.status ?? (evidence.length ? "completed" : "not_started")],
                    ["Proposal", actionProposal?.approval_status ?? "not_started"],
                    ["Execution", actionExecution?.status ?? "not_started"],
                    ["Verification", outcomeVerification?.status ?? "not_started"],
                    ["Resolution", currentResolution?.decision ?? (selected.status === "resolved" ? "resolved" : "open")],
                  ].map(([label, status]) => (
                    <div className="lifecycle-step" key={label}>
                      <span>{label}</span><StatusBadge status={status} />
                    </div>
                  ))}
                </section>
                <section className="panel investigation-controls" aria-labelledby="investigation-controls">
                  <div className="panel-heading">
                    <div><p className="section-kicker">Stage 1</p><h3 id="investigation-controls">Investigation runtimes</h3></div>
                    <p>Alternative runtimes over the same governed read-only capabilities.</p>
                  </div>
                <div className="actions" aria-busy={investigating}>
                  <button
                    className="run"
                    onClick={() => runInvestigation("deterministic")}
                    disabled={investigating}
                  >
                    {investigating && mode === "deterministic"
                      ? "Running deterministic…"
                      : "Run deterministic"}
                  </button>
                  <button
                    className="run ai"
                    onClick={() => runInvestigation("ai")}
                    disabled={investigating || !aiConfigured}
                  >
                    {investigating && mode === "ai"
                      ? "Running AI investigation…"
                      : aiConfigured
                        ? "Run AI investigation"
                        : "AI unavailable"}
                  </button>
                  <button
                    className="run secondary"
                    onClick={() => runInvestigation("agents_sdk")}
                    disabled={investigating || !aiConfigured}
                  >
                    {investigating && mode === "agents_sdk"
                      ? "Running Agents SDK…"
                      : aiConfigured
                        ? "Run Agents SDK"
                        : "Agents SDK unavailable"}
                  </button>
                </div>
                {investigating && (
                  <p className="running" role="status">
                    Investigation in progress. You can select another incident to cancel this view.
                  </p>
                )}
                {mode && !investigating && <p className="mode">Mode: {mode}</p>}
                {investigationError && <p className="error">{investigationError}</p>}
                </section>
                <section className="panel history-panel" aria-labelledby="investigation-history">
                  <div className="panel-heading">
                    <div><p className="section-kicker">Persisted record</p><h3 id="investigation-history">Investigation history</h3></div>
                    <span className="muted">{historyLoading ? "Loading…" : `${investigationRuns.length} runs`}</span>
                  </div>
                  {investigationRuns.length === 0 && !historyLoading ? (
                    <p className="empty-state">No model investigation runs have been recorded for this incident.</p>
                  ) : (
                    <div className="run-history">
                      {investigationRuns.map((run) => (
                        <button className="history-item" key={run.id} onClick={() => loadRun(run)}>
                          <span>
                            <strong>{run.mode === "agents_sdk" ? "Agents SDK" : "Responses API"}</strong>
                            <small>{formatTime(run.created_at)}</small>
                          </span>
                          <StatusBadge status={run.status} />
                        </button>
                      ))}
                    </div>
                  )}
                </section>
                {aiResult && (
                  <article className="panel diagnosis">
                    <div className="diagnosis-heading">
                      <strong>Investigation assessment</strong>
                      {aiMetadata && (
                        <small>
                          {displayStatus(aiMetadata.status)} · {aiMetadata.model}
                        </small>
                      )}
                    </div>
                    <p>{aiResult.summary}</p>
                    <p><b>Assessment:</b> {aiResult.diagnosis}</p>
                    <p><b>Confidence:</b> {Math.round(aiResult.confidence * 100)}%</p>
                    {aiResult.supporting_evidence.length > 0 && (
                      <section className="result-section">
                        <b>Supporting evidence</b>
                        <ul>{aiResult.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
                      </section>
                    )}
                    {aiResult.evidence_ids.length > 0 && (
                      <p><b>Evidence references:</b> {aiResult.evidence_ids.map((id) => `#${id}`).join(", ")}</p>
                    )}
                    <section className="result-section">
                      <b>Recommended next steps</b>
                      <ul>{aiResult.recommended_next_steps.map((item) => <li key={item}>{item}</li>)}</ul>
                    </section>
                    {aiResult.missing_information.length > 0 && (
                      <section className="result-section">
                        <b>Missing information</b>
                        <ul>{aiResult.missing_information.map((item) => <li key={item}>{item}</li>)}</ul>
                      </section>
                    )}
                    {aiResult.human_action_required && (
                      <p className="human-control">
                        Human action required — this investigation recommends next steps but does not execute remediation.
                      </p>
                    )}
                  </article>
                )}
                {proposalError && <p className="error error-banner">{proposalError}</p>}
                {actionProposal && (
                  <article className="panel proposal-panel" aria-labelledby="proposed-action">
                    <div className="panel-heading">
                      <div><p className="section-kicker">AI proposes · Human authorizes</p><h3 id="proposed-action">Proposed Action</h3></div>
                      <StatusBadge status={actionProposal.approval_status} />
                    </div>
                    <p><b>Action type:</b> {displayAction(actionProposal.action_type)}</p>
                    <p><b>Target:</b> {actionProposal.target}</p>
                    <div className="result-section">
                      <b>Bounded parameters</b>
                      <pre>{JSON.stringify(actionProposal.parameters, null, 2)}</pre>
                    </div>
                    <p><b>Rationale:</b> {actionProposal.rationale}</p>
                    <p><b>Risk level:</b> {actionProposal.risk_level}</p>
                    <p><b>Supporting evidence:</b> {actionProposal.supporting_evidence_ids.map((id) => `#${id}`).join(", ")}</p>
                    <p><b>Approval state:</b> {displayStatus(actionProposal.approval_status)}</p>
                    {Object.keys(actionProposal.parameters).length > 0 && (
                      <dl className="parameter-list">
                        {Object.entries(actionProposal.parameters).map(([key, value]) => (
                          <div key={key}><dt>{displayStatus(key)}</dt><dd>{String(value)}</dd></div>
                        ))}
                      </dl>
                    )}
                    {actionProposal.approval_status === "pending" && (
                      <div className="actions" aria-busy={decidingProposal}>
                        <button disabled={decidingProposal} onClick={() => decideActionProposal("approve")}>
                          {decidingProposal ? "Recording decision…" : "Approve"}
                        </button>
                        <button disabled={decidingProposal} onClick={() => decideActionProposal("reject")}>Reject</button>
                      </div>
                    )}
                    {actionProposal.approval_status === "approved" &&
                      executionLookupStatus === "not_found" &&
                      !actionExecution && (
                        <section className="result-section" aria-label="Approved action execution">
                          <p className="human-control">Approved, awaiting explicit operator execution.</p>
                          <div className="actions" aria-busy={executingAction}>
                            <button disabled={executingAction} onClick={executeApprovedAction}>
                              {executingAction ? "Execution requested…" : "Execute approved action"}
                            </button>
                          </div>
                        </section>
                      )}
                    {actionProposal.approval_status === "approved" && executionLookupStatus === "loading" && (
                      <p role="status">Checking persisted execution…</p>
                    )}
                    {actionProposal.approval_status === "approved" && executionLookupStatus === "error" && (
                      <p className="human-control">Execution controls are unavailable until persisted state can be confirmed.</p>
                    )}
                    {actionExecution && (
                      <section className="execution-card" aria-label="Execution result">
                        <div className="panel-heading"><h4>Controlled execution</h4><StatusBadge status={actionExecution.status} /></div>
                        <p><b>Capability:</b> {displayAction(actionExecution.capability_name)}</p>
                        <p><b>Execution status:</b> {displayStatus(actionExecution.status).toUpperCase()}</p>
                        {actionExecution.completion_basis && <p><b>Completion basis:</b> {displayStatus(actionExecution.completion_basis)}</p>}
                        {outcomeCertainty && (
                          <div className="attempt-summary">
                            <span>Physical attempt</span><StatusBadge status={outcomeCertainty} />
                            {actionExecutionAttempt && <small>Attempt #{actionExecutionAttempt.id}</small>}
                          </div>
                        )}
                        {actionExecution.result?.data && (
                          <details><summary>Technical result</summary><pre>{JSON.stringify(actionExecution.result.data, null, 2)}</pre></details>
                        )}
                        {actionExecution.result?.data && (
                          <dl>
                            {actionExecution.result.data.target && (
                              <><dt>Target</dt><dd>{actionExecution.result.data.target}</dd></>
                            )}
                            {actionExecution.result.data.previous_state && (
                              <><dt>Previous state</dt><dd>{actionExecution.result.data.previous_state}</dd></>
                            )}
                            {actionExecution.result.data.current_state && (
                              <><dt>Current state</dt><dd>{actionExecution.result.data.current_state}</dd></>
                            )}
                          </dl>
                        )}
                        {actionExecution.status === "failed" && actionExecution.error && (
                          <p className="error">{actionExecution.error.message}</p>
                        )}
                        {actionExecution.status === "running" && (
                          <p className="human-control">Execution is in progress. No additional request will be sent.</p>
                        )}
                        {actionExecution.status === "outcome_unknown" && (
                          <div className="unknown-outcome">
                            <strong>Outcome certainty is unknown</strong>
                            <p>The mutation may have started, so automatic retry is unsafe. The action will not be retried automatically.</p>
                          </div>
                        )}
                      </section>
                    )}
                    {actionExecution && (
                      <section className="result-section execution-timeline" aria-label="Execution Timeline">
                        <h4>Execution Timeline</h4>
                        {timelineLookupStatus === "loading" && (
                          <p role="status">Loading execution timeline…</p>
                        )}
                        {timelineLookupStatus === "error" && (
                          <p className="error" role="alert">Unable to load execution timeline.</p>
                        )}
                        {timelineLookupStatus === "loaded" && executionTimeline.length === 0 && (
                          <p>No persisted lifecycle events are available for this execution.</p>
                        )}
                        {timelineLookupStatus === "loaded" && executionTimeline.length > 0 && (
                          <ol>
                            {executionTimeline.map((entry, index) => (
                              <li key={`${entry.timestamp}-${entry.event_type}-${index}`} className={`timeline-${entry.status ?? "recorded"}`}>
                                <time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time>
                                <p><b>{displayStatus(entry.event_type)}</b></p>
                                <p>{entry.description}</p>
                                <small>
                                  Execution #{entry.execution_id}
                                  {entry.attempt_id ? ` · Attempt #${entry.attempt_id}` : ""}
                                  {entry.status ? ` · ${displayStatus(entry.status).toUpperCase()}` : ""}
                                </small>
                                {entry.reason && <small>Reason: {displayStatus(entry.reason)}</small>}
                              </li>
                            ))}
                          </ol>
                        )}
                      </section>
                    )}
                    {actionExecution?.status === "outcome_unknown" && reconciliationLookupStatus === "loading" && (
                      <p role="status">Checking reconciliation state…</p>
                    )}
                    {actionExecution?.status === "outcome_unknown" &&
                      reconciliationLookupStatus === "not_found" &&
                      isReconciliationEligible(actionExecutionAttempt) && (
                        <section className="result-section" aria-label="Reconciliation control">
                          <p className="human-control">
                            Reconciliation performs a read-only observation of current state. It never retries the original mutation.
                          </p>
                          <div className="actions" aria-busy={reconciling}>
                            <button disabled={reconciling} onClick={() => reconcileExecution()}>
                              {reconciling ? "Reconciling state…" : "Reconcile state"}
                            </button>
                          </div>
                        </section>
                      )}
                    {reconciliation && (
                      <section className="result-section reconciliation-card" aria-label="Reconciliation">
                        <div className="panel-heading"><h4>Reconciliation</h4><StatusBadge status={reconciliation.status} /></div>
                        <p><b>Reconciliation status:</b> {displayStatus(reconciliation.status).toUpperCase()}</p>
                        {reconciliation.expected_outcome.state && (
                          <p><b>Expected state:</b> {reconciliation.expected_outcome.state.toUpperCase()}</p>
                        )}
                        {reconciliation.observed_outcome?.state && (
                          <p><b>Observed state:</b> {reconciliation.observed_outcome.state.toUpperCase()}</p>
                        )}
                        {reconciliation.status === "desired_state_observed" && (
                          <p className="human-control">The desired technical state is currently observed. This does not prove that the original invocation succeeded.</p>
                        )}
                        {reconciliation.status === "undesired_state_observed" && (
                          <p className="human-control">The desired state is not currently observed. This does not prove that the original mutation did not occur.</p>
                        )}
                        {reconciliation.status === "inconclusive" && (
                          <p className="human-control">A reliable conclusion could not be obtained. The execution remains outcome unknown.</p>
                        )}
                        {reconciliation.status === "running" && !reconciliation.is_stale && (
                          <p className="human-control">A reconciliation exists and is still non-terminal.</p>
                        )}
                        {reconciliation.status === "running" && reconciliation.is_stale && (
                          <p className="human-control">This reconciliation appears stale. Explicit recovery is available as a separate operation and is not started here.</p>
                        )}
                        {reconciliation.recoverable && (
                          <button disabled={reconciling} onClick={() => reconcileExecution(true)}>
                            {reconciling ? "Recovering…" : "Recover stale reconciliation"}
                          </button>
                        )}
                        {reconciliation.status === "inconclusive" && reconciliation.error && (
                          <p className="error">{reconciliation.error.message}</p>
                        )}
                      </section>
                    )}
                    {actionExecution?.status === "completed" && !outcomeVerification && (
                      <div className="actions" aria-busy={verifyingOutcome}>
                        <button disabled={verifyingOutcome} onClick={verifyOutcome}>
                          {verifyingOutcome ? "Checking observed service state…" : "Verify outcome"}
                        </button>
                      </div>
                    )}
                    {outcomeVerification && (
                      <section className="result-section verification-card" aria-label="Outcome verification">
                        <div className="panel-heading">
                          <div><h4>Independent verification</h4><small>New read-only observation of current simulated state</small></div>
                          <StatusBadge status={outcomeVerification.status} />
                        </div>
                        <p><b>Verification status:</b> {displayStatus(outcomeVerification.status).toUpperCase()}</p>
                        <p><b>Expected:</b> {outcomeVerification.expected_outcome.state?.toUpperCase() ?? "UNKNOWN"}</p>
                        {outcomeVerification.observed_outcome?.state && (
                          <p><b>Observed:</b> {outcomeVerification.observed_outcome.state.toUpperCase()}</p>
                        )}
                        {outcomeVerification.error && (
                          <p className="error">{outcomeVerification.error.message}</p>
                        )}
                        {outcomeVerification.status === "not_verified" && (
                          <p className="human-control">Incident remains open because the expected outcome was not observed.</p>
                        )}
                        {outcomeVerification.status === "failed" && (
                          <p className="human-control">Reliable post-execution evidence could not be collected. Incident remains open.</p>
                        )}
                      </section>
                    )}
                    {outcomeVerification?.status === "verified" &&
                      selected.status !== "resolved" &&
                      !resolutionDecisions.some((item) => item.verification_id === outcomeVerification.id) && (
                        <section className="result-section resolution-card" aria-label="Resolution review">
                          <div className="panel-heading">
                            <div><h4>Resolution Review</h4><small>Verification does not resolve the incident automatically.</small></div>
                            <StatusBadge status="open" />
                          </div>
                          <label htmlFor="resolution-reason">Reason</label>
                          <textarea
                            id="resolution-reason"
                            maxLength={1000}
                            value={resolutionReason}
                            onChange={(event) => setResolutionReason(event.target.value)}
                          />
                          <div className="actions" aria-busy={decidingResolution}>
                            <button disabled={decidingResolution} onClick={() => decideResolution("resolve")}>
                              {decidingResolution ? "Recording decision…" : "Resolve incident"}
                            </button>
                            <button disabled={decidingResolution} onClick={() => decideResolution("keep_open")}>
                              Keep open
                            </button>
                          </div>
                        </section>
                      )}
                    <p className="human-control">
                      Approval permits one attempt of this exact action; execution remains policy-controlled and deterministic.
                    </p>
                  </article>
                )}
                {resolutionDecisions.length > 0 && (
                  <section className="panel" aria-label="Resolution history">
                    <h3>Resolution History</h3>
                    {resolutionDecisions.map((decision) => (
                      <article key={decision.id}>
                        <p><b>Decision:</b> {displayStatus(decision.decision).toUpperCase()}</p>
                        <p><b>Verification evidence:</b> #{decision.verification_id}</p>
                        {decision.reason && <p><b>Reason:</b> {decision.reason}</p>}
                      </article>
                    ))}
                  </section>
                )}
                {steps.length > 0 && (
                  <section className="panel" aria-labelledby="investigation-steps">
                    <h3 id="investigation-steps">Investigation</h3>
                    <ul>
                      {steps.map((step) => (
                        <li key={step.id}>{step.tool} · {step.target_resource} · {displayStatus(step.status)}</li>
                      ))}
                    </ul>
                  </section>
                )}
                {evidence.length > 0 && (
                  <section className="panel evidence-panel" aria-labelledby="investigation-evidence">
                    <div className="panel-heading">
                      <div><p className="section-kicker">Factual observations</p><h3 id="investigation-evidence">Evidence</h3></div>
                      <span className="count">{evidence.length}</span>
                    </div>
                    {evidence.map((item) => (
                      <article key={item.id}>
                        <strong>#{item.id} · {item.source}</strong>
                        <small>{item.resource}</small>
                        <details><summary>Observed payload</summary><pre>{JSON.stringify(item.payload, null, 2)}</pre></details>
                      </article>
                    ))}
                  </section>
                )}
                {events.length > 0 && (
                  <section className="panel timeline-panel" aria-labelledby="operational-timeline">
                    <div className="panel-heading">
                      <div><p className="section-kicker">Persisted lifecycle</p><h3 id="operational-timeline">Operational timeline</h3></div>
                      <span className="count">{events.length}</span>
                    </div>
                    <ol className="timeline">
                      {events.map((event) => (
                        <li key={event.id}>
                          <span className={`timeline-marker ${toneFor(event.status ?? event.event_type)}`} />
                          <div>
                            <strong>{displayStatus(event.event_type)}</strong>
                            <small>{formatTime(event.timestamp)} · {displayStatus(event.runtime)}</small>
                          </div>
                          {event.status && <StatusBadge status={event.status} />}
                        </li>
                      ))}
                    </ol>
                  </section>
                )}
              </>
            ) : (
              <div className="empty-selection">
                <span>01</span>
                <h2>Select an incident</h2>
                <p>Choose an item from the queue to review its operational lifecycle.</p>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
