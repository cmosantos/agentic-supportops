export type Health = { status: string; service: string };
export type Incident = {
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
export type InvestigationOrigin = "deterministic" | "ai" | "agents_sdk";
export type Evidence = {
  id: number;
  incident_id: number;
  investigation_id: number | null;
  source: string;
  resource: string;
  origin: InvestigationOrigin;
  payload: Record<string, unknown>;
  created_at: string;
};
export type InvestigationStep = {
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
export type AIStatus = "running" | "completed" | "insufficient_evidence" | "failed";
export type AIResult = {
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
export type ActionProposalInput = {
  action_type: string;
  target: string;
  parameters: Record<string, unknown>;
  rationale: string;
  supporting_evidence_ids: number[];
  risk_level: "low" | "medium" | "high";
};
export type ActionProposal = ActionProposalInput & {
  id: number;
  investigation_id: number;
  incident_id: number;
  approval_status: "pending" | "approved" | "rejected";
  created_at: string;
  decision_at: string | null;
  rejection_reason: string | null;
};
export type ActionExecution = {
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
export type InvestigationEvent = {
  id: number;
  investigation_id: number;
  runtime: "manual_responses" | "agents_sdk";
  event_type: string;
  sequence: number;
  status: string | null;
  timestamp: string;
  metadata: Record<string, unknown>;
};
export type InvestigationRun = AIExecution["investigation"];
export type ActionExecutionAttempt = {
  id: number;
  execution_id: number;
  attempt_number: number;
  status: "running" | "completed" | "failed" | "outcome_unknown";
  claimed_at: string;
  invocation_started_at: string | null;
  completed_at: string | null;
  failure_cause: string | null;
  outcome_certainty: "applied_acknowledged" | "not_applied" | "unknown" | "legacy_undetermined" | null;
};
export type Reconciliation = {
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
export type OutcomeVerification = {
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
export type IncidentResolutionDecision = {
  id: number;
  incident_id: number;
  verification_id: number;
  execution_id: number;
  proposal_id: number;
  decision: "resolve" | "keep_open";
  reason: string | null;
  decided_at: string;
};
export type ActionExecutionTimelineEntry = {
  timestamp: string;
  event_type: string;
  execution_id: number;
  attempt_id: number | null;
  status: string | null;
  description: string;
  reason: string | null;
};
export type Investigation = {
  incident_id: number;
  catalog_id: string | null;
  steps: InvestigationStep[];
  evidence: Evidence[];
};
export type AIExecution = {
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
