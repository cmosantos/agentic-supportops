import type {
  Health,
  Incident,
  Investigation,
  AIExecution,
  InvestigationRun,
  InvestigationEvent,
  ActionProposalInput,
  ActionProposal,
  ActionExecution,
  ActionExecutionAttempt,
  Reconciliation,
  OutcomeVerification,
  IncidentResolutionDecision,
  ActionExecutionTimelineEntry,
} from "../types/supportOps";

export type ApiResult<T> =
  | { ok: true; status: number; data: T }
  | { ok: false; status: number; error: string };

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

async function request<T>(path: string, init?: RequestInit): Promise<ApiResult<T>> {
  const response = await fetch(`${apiBaseUrl}${path}`, init);
  if (!response.ok) return { ok: false, status: response.status, error: await investigationErrorMessage(response) };
  return { ok: true, status: response.status, data: await response.json() as T };
}

const runPath = (incident: string | number, runId: number) =>
  `/incidents/${incident}/investigation-runs/${runId}`;
const proposalPath = (incident: string | number, proposal: ActionProposal) =>
  `${runPath(incident, proposal.investigation_id)}/action-proposals/${proposal.id}`;

export const supportOpsApi = {
  getHealth: (signal?: AbortSignal) => request<Health>("/health", { signal }),
  getIncidents: (signal?: AbortSignal) => request<Incident[]>("/incidents", { signal }),
  getAIConfiguration: (signal?: AbortSignal) => request<{ configured: boolean }>("/ai/config", { signal }),
  getInvestigation: (incident: string | number, signal?: AbortSignal) => request<Investigation>(`/incidents/${incident}/investigation`, { signal }),
  getInvestigationRuns: (incident: string | number) => request<InvestigationRun[]>(`/incidents/${incident}/investigation-runs`),
  getArtifacts: (incident: string | number, runId: number, signal?: AbortSignal) => request<AIExecution>(`${runPath(incident, runId)}/artifacts`, { signal }),
  getEvents: (incident: string | number, runId: number, signal?: AbortSignal) => request<InvestigationEvent[]>(`${runPath(incident, runId)}/events`, { signal }),
  investigate: <T extends Investigation | AIExecution>(incident: string | number, endpoint: "investigate" | "investigate-ai" | "investigate-agent-sdk", signal: AbortSignal) => request<T>(`/incidents/${incident}/${endpoint}`, { method: "POST", signal }),
  getProposals: (incident: string | number, runId: number, signal?: AbortSignal) => request<ActionProposal[]>(`${runPath(incident, runId)}/action-proposals`, { signal }),
  createProposal: (incident: string | number, runId: number, proposal: ActionProposalInput, signal?: AbortSignal) => request<ActionProposal>(`${runPath(incident, runId)}/action-proposals`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(proposal), signal,
  }),
  decideProposal: (incident: string | number, proposal: ActionProposal, decision: "approve" | "reject") => request<ActionProposal>(`${proposalPath(incident, proposal)}/${decision}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: decision === "reject" ? JSON.stringify({ reason: "Rejected by the human operator." }) : undefined,
  }),
  getExecution: (incident: string | number, proposal: ActionProposal, signal?: AbortSignal) => request<ActionExecution>(`${proposalPath(incident, proposal)}/execution`, { signal }),
  executeProposal: (incident: string | number, proposal: ActionProposal) => request<ActionExecution>(`${proposalPath(incident, proposal)}/execute`, { method: "POST" }),
  getExecutionAttempt: (executionId: number, signal?: AbortSignal) => request<ActionExecutionAttempt>(`/action-executions/${executionId}/attempt`, { signal }),
  getExecutionTimeline: (executionId: number, signal?: AbortSignal) => request<ActionExecutionTimelineEntry[]>(`/action-executions/${executionId}/timeline`, { signal }),
  getReconciliation: (executionId: number, attemptId: number, signal?: AbortSignal) => request<Reconciliation>(`/action-executions/${executionId}/attempts/${attemptId}/reconciliation`, { signal }),
  reconcileExecution: (executionId: number, attemptId: number) => request<Reconciliation>(`/action-executions/${executionId}/attempts/${attemptId}/reconcile`, { method: "POST" }),
  getVerification: (executionId: number) => request<OutcomeVerification>(`/action-executions/${executionId}/verification`),
  verifyExecution: (executionId: number) => request<OutcomeVerification>(`/action-executions/${executionId}/verify`, { method: "POST" }),
  getResolutionDecisions: (incident: string | number) => request<IncidentResolutionDecision[]>(`/incidents/${incident}/resolution-decisions`),
  decideResolution: (incident: string | number, decision: Pick<IncidentResolutionDecision, "verification_id" | "decision" | "reason">) => request<IncidentResolutionDecision>(`/incidents/${incident}/resolution-decisions`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(decision),
  }),
};
