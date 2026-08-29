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
type InvestigationOrigin = "deterministic" | "ai";
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
  status: "running" | "completed" | "failed";
  requested_at: string;
  started_at: string;
  completed_at: string | null;
  result: { data?: Record<string, unknown> } | null;
  error: { code: string; message: string } | null;
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
type InvestigationMode = "deterministic" | "ai";
type AIMetadata = { status: AIStatus; model: string };

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
  const [actionProposal, setActionProposal] = useState<ActionProposal | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [decidingProposal, setDecidingProposal] = useState(false);
  const [actionExecution, setActionExecution] = useState<ActionExecution | null>(null);
  const [executingAction, setExecutingAction] = useState(false);
  const [outcomeVerification, setOutcomeVerification] = useState<OutcomeVerification | null>(null);
  const [verifyingOutcome, setVerifyingOutcome] = useState(false);
  const investigationRequest = useRef<AbortController | null>(null);
  const investigationVersion = useRef(0);

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
    setActionProposal(null);
    setProposalError(null);
    setActionExecution(null);
    setExecutingAction(false);
    setOutcomeVerification(null);
    setVerifyingOutcome(false);
    setMode(null);
    setInvestigationError(null);
    setInvestigating(false);
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
    setExecutingAction(false);
    setOutcomeVerification(null);
    setVerifyingOutcome(false);
    setMode(investigationMode);

    try {
      const reference = selected.catalog_id ?? selected.id;
      const endpoint = investigationMode === "ai" ? "investigate-ai" : "investigate";
      const response = await fetch(`${apiBaseUrl}/incidents/${reference}/${endpoint}`, {
        method: "POST",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await investigationErrorMessage(response));
      if (requestVersion !== investigationVersion.current) return;

      if (investigationMode === "ai") {
        const result: AIExecution = await response.json();
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
        setSteps(result.steps);
        setAiResult(result.investigation.result);
        setAiMetadata({
          status: result.investigation.status,
          model: result.investigation.model,
        });
        if (result.investigation.result?.proposed_action) {
          const proposalResponse = await fetch(
            `${apiBaseUrl}/incidents/${reference}/investigation-runs/${result.investigation.id}/action-proposals`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(result.investigation.result.proposed_action),
              signal: controller.signal,
            },
          );
          if (!proposalResponse.ok) {
            setProposalError(await investigationErrorMessage(proposalResponse));
          } else if (requestVersion === investigationVersion.current) {
            setActionProposal(await proposalResponse.json());
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
    if (!selected || !actionProposal || decidingProposal) return;
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
      setDecidingProposal(false);
    }
  }

  async function executeApprovedAction() {
    if (!selected || !actionProposal || executingAction || actionExecution) return;
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
      setExecutingAction(false);
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
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Outcome verification failed");
    } finally {
      setVerifyingOutcome(false);
    }
  }

  return (
    <main>
      <section className="shell">
        <p className="eyebrow">IT Support &amp; Operations</p>
        <h1>Agentic SupportOps</h1>
        <p className="summary">Deterministic and model-driven incident investigation.</p>
        <div className="health" aria-live="polite">
          <span className={health ? "indicator online" : "indicator"} />
          {health
            ? `Backend online — ${health.service}`
            : unavailable
              ? "Backend unavailable"
              : "Checking backend health…"}
        </div>
        <div className="workspace">
          <div>
            <h2>Incident catalog</h2>
            <div className="incident-list">
              {incidents.map((incident) => (
                <button
                  className={selected?.id === incident.id ? "incident selected" : "incident"}
                  key={incident.id}
                  onClick={() => selectIncident(incident)}
                >
                  <strong>{incident.catalog_id ?? `#${incident.id}`}</strong>
                  <span>{incident.title}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="details">
            {selected ? (
              <>
                <h2>{selected.title}</h2>
                <p>{selected.description}</p>
                <p className="metadata">
                  {selected.category} · {selected.priority} · {selected.affected_resource_id}
                </p>
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
                </div>
                {investigating && (
                  <p className="running" role="status">
                    Investigation in progress. You can select another incident to cancel this view.
                  </p>
                )}
                {mode && !investigating && <p className="mode">Mode: {mode}</p>}
                {investigationError && <p className="error">{investigationError}</p>}
                {aiResult && (
                  <article className="diagnosis">
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
                {proposalError && <p className="error">{proposalError}</p>}
                {actionProposal && (
                  <article className="diagnosis" aria-labelledby="proposed-action">
                    <h3 id="proposed-action">Proposed Action</h3>
                    <p><b>Action type:</b> {displayStatus(actionProposal.action_type)}</p>
                    <p><b>Target:</b> {actionProposal.target}</p>
                    <p><b>Rationale:</b> {actionProposal.rationale}</p>
                    <p><b>Risk level:</b> {actionProposal.risk_level}</p>
                    <p><b>Supporting evidence:</b> {actionProposal.supporting_evidence_ids.map((id) => `#${id}`).join(", ")}</p>
                    <p><b>Approval state:</b> {displayStatus(actionProposal.approval_status)}</p>
                    {actionProposal.approval_status === "pending" && (
                      <div className="actions">
                        <button disabled={decidingProposal} onClick={() => decideActionProposal("approve")}>Approve</button>
                        <button disabled={decidingProposal} onClick={() => decideActionProposal("reject")}>Reject</button>
                      </div>
                    )}
                    {actionProposal.approval_status === "approved" &&
                      actionProposal.action_type === "restart_simulated_service" &&
                      !actionExecution && (
                        <div className="actions" aria-busy={executingAction}>
                          <button disabled={executingAction} onClick={executeApprovedAction}>
                            {executingAction ? "Executing…" : "Execute approved action"}
                          </button>
                        </div>
                      )}
                    {actionExecution && (
                      <section className="result-section" aria-label="Execution result">
                        <p><b>Capability:</b> {displayStatus(actionExecution.capability_name)}</p>
                        <p><b>Execution status:</b> {actionExecution.status.toUpperCase()}</p>
                        {actionExecution.result?.data && (
                          <pre>{JSON.stringify(actionExecution.result.data, null, 2)}</pre>
                        )}
                        {actionExecution.error && (
                          <p className="error">{actionExecution.error.message}</p>
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
                      <section className="result-section" aria-label="Outcome verification">
                        <h4>Verification</h4>
                        <p><b>Verification status:</b> {displayStatus(outcomeVerification.status).toUpperCase()}</p>
                        <p><b>Expected:</b> {outcomeVerification.expected_outcome.state?.toUpperCase() ?? "UNKNOWN"}</p>
                        {outcomeVerification.observed_outcome?.state && (
                          <p><b>Observed:</b> {outcomeVerification.observed_outcome.state.toUpperCase()}</p>
                        )}
                        {outcomeVerification.error && (
                          <p className="error">{outcomeVerification.error.message}</p>
                        )}
                      </section>
                    )}
                    <p className="human-control">
                      Approval permits one attempt of this exact action; execution remains policy-controlled and deterministic.
                    </p>
                  </article>
                )}
                {steps.length > 0 && (
                  <section className="result-section" aria-labelledby="investigation-steps">
                    <h3 id="investigation-steps">Investigation</h3>
                    <ul>
                      {steps.map((step) => (
                        <li key={step.id}>{step.tool} · {step.target_resource} · {displayStatus(step.status)}</li>
                      ))}
                    </ul>
                  </section>
                )}
                {evidence.length > 0 && (
                  <section className="result-section" aria-labelledby="investigation-evidence">
                    <h3 id="investigation-evidence">Evidence</h3>
                    {evidence.map((item) => (
                      <article key={item.id}>
                        <strong>#{item.id} · {item.source}</strong>
                        <small>{item.resource}</small>
                        <pre>{JSON.stringify(item.payload, null, 2)}</pre>
                      </article>
                    ))}
                  </section>
                )}
              </>
            ) : (
              <p>Select an incident to inspect it.</p>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
