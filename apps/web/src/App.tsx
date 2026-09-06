import { useEffect, useRef, useState } from "react";
import { IncidentPicker } from "./components/IncidentList";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { InvestigationHistory } from "./components/InvestigationHistory";
import { InvestigationReview, type InvestigationReviewMode } from "./components/InvestigationReview";
import { StatusBadge, displayStatus, formatTime, toneFor } from "./components/supportOpsPresentation";
import { useOperatorWorkflow } from "./hooks/useOperatorWorkflow";
import { supportOpsApi } from "./api/supportOpsApi";
import type { Health, Incident, Evidence, InvestigationStep, AIStatus, AIResult, ActionProposal, InvestigationEvent, InvestigationRun, Investigation, AIExecution } from "./types/supportOps";

type InvestigationMode = "deterministic" | "ai" | "agents_sdk";
type AIMetadata = { status: AIStatus; model: string };
type InvestigationEndpoint = "investigate" | "investigate-ai" | "investigate-agent-sdk";

const runtimeLabels: Record<InvestigationMode, string> = {
  deterministic: "Deterministic",
  ai: "AI",
  agents_sdk: "Agents SDK",
};

const investigationEndpoints: Record<InvestigationMode, InvestigationEndpoint> = {
  deterministic: "investigate",
  ai: "investigate-ai",
  agents_sdk: "investigate-agent-sdk",
};

function isCapabilityLimitation(message: string, runtime: InvestigationMode): boolean {
  const normalized = message.toLowerCase();
  return runtime === "deterministic" && (normalized.includes("no deterministic playbook") || normalized.includes("investigation_not_supported"));
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
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [reviewingHistoricalRun, setReviewingHistoricalRun] = useState(false);
  const [selectedRuntime, setSelectedRuntime] = useState<InvestigationMode>("deterministic");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerIncidentId, setPickerIncidentId] = useState<number | null>(null);
  const [pickerRuntime, setPickerRuntime] = useState<InvestigationMode | null>(null);
  const investigationRequest = useRef<AbortController | null>(null);
  const reviewRequest = useRef<AbortController | null>(null);
  const reviewVersion = useRef(0);
  const investigationVersion = useRef(0);
  const workflow = useOperatorWorkflow({
    selected,
    investigationVersion,
    onEventsLoaded: setEvents,
    onIncidentResolved: (incidentId) => {
      setSelected((current) => current ? { ...current, status: "resolved" } : current);
      setIncidents((current) => current.map((item) => item.id === incidentId ? { ...item, status: "resolved" } : item));
    },
  });
  const { actionProposal, actionExecution, outcomeVerification, currentResolution } = workflow;
  const reviewRun = selectedRunId === null ? null : investigationRuns.find((run) => run.id === selectedRunId) ?? null;
  const reviewMode: InvestigationReviewMode | null = mode === "deterministic" ? "deterministic" : mode === "agents_sdk" ? "agents_sdk" : mode === "ai" ? "ai" : null;
  const currentState = currentResolution?.decision === "resolve"
    ? "Incident resolved by human"
    : currentResolution?.decision === "keep_open"
      ? "Incident kept open by human"
      : outcomeVerification?.status === "verified"
        ? "Ready for human resolution"
        : outcomeVerification?.status === "failed" || outcomeVerification?.status === "not_verified"
          ? "Verification failed"
          : outcomeVerification?.status === "running"
            ? "Verification in progress"
            : actionExecution?.status === "outcome_unknown"
              ? "Outcome uncertain"
              : actionExecution?.status === "running"
                ? "Execution in progress"
                : actionExecution?.status === "completed"
                  ? "Execution completed"
                  : actionExecution?.status === "failed"
                    ? "Execution failed"
                    : actionProposal?.approval_status === "rejected"
                      ? "Proposal rejected"
                      : actionProposal?.approval_status === "pending"
                        ? "Awaiting human approval"
                        : actionProposal?.approval_status === "approved"
                          ? "Approved for execution"
                          : aiResult
                            ? "Investigation completed"
                            : evidence.length > 0
                              ? "Evidence collected"
                              : investigating
                                ? "Investigation in progress"
                                : "Awaiting investigation";
  const nextAction = currentResolution
    ? "Resolution recorded"
    : outcomeVerification?.status === "verified"
      ? "Resolve incident"
      : outcomeVerification
        ? "Review verification result"
        : actionExecution?.status === "outcome_unknown"
          ? "Reconcile uncertain outcome"
          : actionExecution?.status === "completed"
            ? "Verify outcome"
            : actionExecution?.status === "running"
              ? "Wait for execution result"
              : actionProposal?.approval_status === "rejected"
                ? "Review findings"
                : actionProposal?.approval_status === "pending"
                  ? "Approve proposed action"
                  : actionProposal?.approval_status === "approved"
                    ? "Execute approved action"
                    : evidence.length > 0 || aiResult
                      ? "Review proposal"
                      : "Run investigation";
  const stateDescription = currentResolution
    ? "The final human decision is recorded for this incident."
    : outcomeVerification?.status === "verified"
      ? "An independent read confirms the expected technical outcome."
      : outcomeVerification?.status === "failed" || outcomeVerification?.status === "not_verified"
        ? "The independent check did not prove the expected outcome; the incident remains open."
        : outcomeVerification?.status === "running"
          ? "The independent check is in progress."
          : actionExecution?.status === "outcome_unknown"
            ? "The physical attempt has no reliable outcome; read-only discovery is required before mutation."
            : actionExecution?.status === "completed"
              ? "The approved mutation was acknowledged; independent verification is still outstanding."
              : actionExecution?.status === "failed"
                ? "The controlled execution failed; no successful outcome is implied."
                : actionProposal?.approval_status === "rejected"
                  ? "The proposed mutation was rejected and cannot proceed to execution."
                  : actionProposal?.approval_status === "pending"
                    ? "A bounded action is proposed from the persisted investigation evidence."
                    : actionProposal?.approval_status === "approved"
                      ? "The exact approved action is ready for explicit operator execution."
                      : aiResult
                        ? "The system synthesized the investigation findings from the evidence shown below."
                        : "Start with a read-only investigation to collect grounded evidence.";

  useEffect(() => {
    const controller = new AbortController();
    async function loadCoreApplication() {
      try {
        const [healthResponse, incidentResponse] = await Promise.all([supportOpsApi.getHealth(controller.signal), supportOpsApi.getIncidents(controller.signal)]);
        if (!healthResponse.ok || !incidentResponse.ok) throw new Error("Core application data failed");
        setHealth(healthResponse.data);
        setIncidents(incidentResponse.data);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      }
    }
    async function loadAIConfiguration() {
      try {
        const response = await supportOpsApi.getAIConfiguration(controller.signal);
        if (response.ok) setAiConfigured(response.data.configured);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setAiConfigured(false);
      }
    }
    void loadCoreApplication();
    void loadAIConfiguration();
    return () => { controller.abort(); investigationRequest.current?.abort(); investigationVersion.current += 1; };
  }, []);

  function activateIncident(incident: Incident) {
    investigationRequest.current?.abort();
    reviewRequest.current?.abort();
    investigationRequest.current = null;
    reviewRequest.current = null;
    investigationVersion.current += 1;
    reviewVersion.current += 1;
    setSelected(incident); setEvidence([]); setSteps([]); setAiResult(null); setAiMetadata(null); setInvestigationRuns([]); setEvents([]);
    setHistoryLoading(true); setSelectedRunId(null); setReviewLoading(false); setReviewError(null); setReviewingHistoricalRun(false);
    workflow.reset(); setMode(null); setSelectedRuntime("deterministic"); setInvestigationError(null); setInvestigating(false);
    const selectionVersion = investigationVersion.current;
    const reference = incident.catalog_id ?? incident.id;
    void workflow.loadResolutionHistory(reference, selectionVersion);
    void supportOpsApi.getInvestigationRuns(reference)
      .then(async (response) => response.ok ? response.data : [])
      .then((runs: InvestigationRun[]) => {
        if (selectionVersion !== investigationVersion.current) return;
        setInvestigationRuns((current) => [
          ...current.filter((item) => !runs.some((run) => run.id === item.id)),
          ...runs,
        ]);
      })
      .catch(() => undefined)
      .finally(() => {
        if (selectionVersion === investigationVersion.current) setHistoryLoading(false);
      });
    return selectionVersion;
  }

  function openIncidentPicker() {
    setPickerIncidentId(selected?.id ?? null);
    setPickerRuntime(null);
    setPickerOpen(true);
  }

  function selectAndRunIncident() {
    const incident = incidents.find((item) => item.id === pickerIncidentId);
    if (!incident || !pickerRuntime || (pickerRuntime !== "deterministic" && !aiConfigured)) return;
    const selectionVersion = activateIncident(incident);
    setSelectedRuntime(pickerRuntime);
    setPickerOpen(false);
    void runInvestigation(pickerRuntime, incident, selectionVersion);
  }

  async function loadRun(run: InvestigationRun) {
    if (!selected) return;
    reviewRequest.current?.abort();
    const controller = new AbortController();
    reviewRequest.current = controller;
    const version = investigationVersion.current;
    const runVersion = ++reviewVersion.current;
    const reference = selected.catalog_id ?? selected.id;
    setReviewLoading(true); setReviewError(null); setSelectedRunId(run.id); setReviewingHistoricalRun(true); workflow.setProposalError(null);
    try {
      const [artifactsResponse, eventsResponse, proposalsResponse] = await Promise.all([supportOpsApi.getArtifacts(reference, run.id, controller.signal), supportOpsApi.getEvents(reference, run.id, controller.signal), supportOpsApi.getProposals(reference, run.id, controller.signal)]);
      if (!artifactsResponse.ok || !eventsResponse.ok || !proposalsResponse.ok) throw new Error("Historical investigation details could not be loaded");
      const artifacts: AIExecution = artifactsResponse.data;
      if (version !== investigationVersion.current || runVersion !== reviewVersion.current) return;
      setEvidence(artifacts.evidence); setSteps(artifacts.steps); setAiResult(artifacts.investigation.result); setAiMetadata({ status: artifacts.investigation.status, model: artifacts.investigation.model });
      const historicalMode = artifacts.investigation.mode === "agents_sdk" ? "agents_sdk" : "ai";
      setMode(historicalMode); setSelectedRuntime(historicalMode); setEvents(eventsResponse.data); workflow.showHistoricalProposal(proposalsResponse.data.at(-1) ?? null);
    } catch (error: unknown) {
      if (!controller.signal.aborted && version === investigationVersion.current && runVersion === reviewVersion.current) setReviewError(error instanceof Error ? error.message : "History loading failed");
    } finally {
      if (!controller.signal.aborted && version === investigationVersion.current && runVersion === reviewVersion.current) setReviewLoading(false);
    }
  }

  async function runInvestigation(
    investigationMode: InvestigationMode,
    incident: Incident | null = selected,
    selectionVersion?: number,
  ) {
    if (!incident || investigationRequest.current) return;
    if (selectionVersion !== undefined && selectionVersion !== investigationVersion.current) return;
    const controller = new AbortController();
    reviewRequest.current?.abort(); reviewRequest.current = null; reviewVersion.current += 1;
    const requestVersion = selectionVersion ?? ++investigationVersion.current;
    investigationRequest.current = controller;
    setReviewLoading(false);
    setEvents([]);
    setInvestigating(true); setInvestigationError(null); setReviewError(null); setReviewingHistoricalRun(false); setSelectedRunId(null); setEvidence([]); setSteps([]); setAiResult(null); setAiMetadata(null); workflow.reset(); setMode(investigationMode); setSelectedRuntime(investigationMode);
    try {
      const reference = incident.catalog_id ?? incident.id;
      const response = await supportOpsApi.investigate<AIExecution | Investigation>(reference, investigationEndpoints[investigationMode], controller.signal);
      if (!response.ok) throw new Error(response.error);
      if (requestVersion !== investigationVersion.current) return;
      if (investigationMode !== "deterministic") {
        const result = response.data as AIExecution;
        setEvidence(result.evidence); setSteps(result.steps); setAiResult(result.investigation.result); setAiMetadata({ status: result.investigation.status, model: result.investigation.model }); setSelectedRunId(result.investigation.id);
        setInvestigationRuns((current) => [result.investigation, ...current.filter((item) => item.id !== result.investigation.id)]);
        void supportOpsApi.getEvents(reference, result.investigation.id, controller.signal).then(async (eventResponse) => eventResponse.ok ? eventResponse.data : []).then((items: InvestigationEvent[]) => { if (requestVersion === investigationVersion.current) setEvents(items); }).catch(() => undefined);
        if (result.investigation.result?.proposed_action) await workflow.loadProposedAction(reference, result.investigation.id, result.investigation.result.proposed_action, controller.signal, requestVersion);
      } else {
        const result = response.data as Investigation;
        setEvidence(result.evidence); setSteps(result.steps); setSelectedRunId(null);
      }
    } catch (error: unknown) {
      if (controller.signal.aborted || requestVersion !== investigationVersion.current) return;
      setInvestigationError(error instanceof Error ? error.message : "Investigation failed");
    } finally {
      if (requestVersion === investigationVersion.current) { investigationRequest.current = null; setInvestigating(false); }
    }
  }

  return (
    <main id="console">
      <a className="skip-link" href="#operator-workspace">Skip to operator workspace</a>
      <section className="shell">
        <header className="app-header">
          <div className="brand"><span className="brand-mark" aria-hidden="true">A<span>•</span></span><div><p className="eyebrow">Operator Console</p><h1>Agentic SupportOps</h1><p className="summary">Investigate, govern, verify.</p></div></div>
          <div className="system-status" aria-label="System status"><div className="health" aria-live="polite"><span className={health ? "indicator online" : "indicator"} />{health ? `Backend online — ${health.service}` : unavailable ? "Backend unavailable" : "Checking backend health…"}</div><span className="system-chip">Contoso simulation</span><span className={aiConfigured ? "system-chip available" : "system-chip"}>AI · {aiConfigured ? "available" : "not configured"}</span></div>
        </header>
        <div className="workspace">
          <div className="details" id="operator-workspace" tabIndex={-1}>
            {selected ? <>
              <header className="incident-header"><div><p className="section-kicker">{selected.catalog_id ?? `Incident #${selected.id}`}</p><h2>{selected.title}</h2><p className="incident-description">{selected.description}</p></div><div className="incident-header-status"><StatusBadge status={selected.status} /><span>{selected.priority} priority</span><button type="button" className="incident-picker-trigger" onClick={openIncidentPicker}>Change incident</button></div></header>
              <dl className="incident-facts"><div><dt>Severity</dt><dd><StatusBadge status={selected.priority} /></dd></div><div><dt>Affected resource</dt><dd>{selected.affected_resource_id ?? "Not specified"}</dd></div><div><dt>Category</dt><dd>{selected.category}</dd></div><div><dt>Updated</dt><dd>{formatTime(selected.updated_at)}</dd></div></dl>
              <p className="sr-only"><b>Incident status:</b> {selected.status.toUpperCase()}</p>
              <section className="lifecycle" aria-label="Operational lifecycle">{[["Incident", selected.status], ["Investigation", investigating ? "running" : aiMetadata?.status ?? (steps.length ? "recorded" : "not_loaded")], ["Evidence", evidence.length ? `${evidence.length} records` : "not_loaded"], ["Proposal", actionProposal ? "recorded" : "not_loaded"], ["Human Approval", actionProposal?.approval_status ?? "not_loaded"], ["Execution", actionExecution?.status ?? "not_loaded"], ["Attempt", !reviewingHistoricalRun ? workflow.actionExecutionAttempt?.status ?? "not_loaded" : "not_loaded"], ["Verification", outcomeVerification?.status ?? "not_loaded"], ["Human Resolution", currentResolution?.decision ?? "not_loaded"]].map(([label, status], index) => { const future = status === "not_loaded"; return <div className={`lifecycle-step ${future ? "future" : toneFor(String(status))}`} key={label}><span className="step-index">{String(index + 1).padStart(2, "0")}</span><span className="step-label">{label}</span><span className="step-state"><span className="step-marker" aria-hidden="true" /><span className="sr-only">{future ? "Upcoming" : displayStatus(String(status))}</span></span></div>; })}</section>
              {!reviewingHistoricalRun && (actionExecution?.status === "outcome_unknown" || actionExecution?.completion_basis === "reconciliation") && <aside className="reconciliation-branch" aria-label="Uncertain outcome path"><span className="branch-icon" aria-hidden="true">↳</span><div><strong>Attempt → Outcome uncertain → Reconciliation → Verification</strong><p>Read current state before any further mutation.</p></div><StatusBadge status={workflow.reconciliation?.status ?? "outcome_unknown"} /></aside>}
              <section className="current-state" aria-label="Current operational state"><div><p className="section-kicker">Current state</p><h3>{currentState}</h3><p className="state-description">{stateDescription}</p><p className="state-detail">{evidence.length} evidence record{evidence.length === 1 ? "" : "s"} available</p></div><div className="next-action"><span>Next action</span><strong>{nextAction}</strong></div></section>
              <section className={`runtime-panel ${actionExecution || actionProposal ? "runtime-context" : ""}`} aria-labelledby="investigation-controls">
                <div className="section-heading"><div><p className="section-kicker">Finding</p><h3 id="investigation-controls">Choose a runtime</h3></div><span className="muted">Read-only</span></div>
                <div className="runtime-control" aria-busy={investigating}><label className="runtime-select" htmlFor="investigation-runtime"><span>Runtime</span><select id="investigation-runtime" aria-label="Investigation runtime" value={selectedRuntime} onChange={(event) => setSelectedRuntime(event.target.value as InvestigationMode)} disabled={investigating} aria-describedby="runtime-help"><option value="deterministic">{runtimeLabels.deterministic}</option><option value="ai" disabled={!aiConfigured}>AI</option><option value="agents_sdk" disabled={!aiConfigured}>{runtimeLabels.agents_sdk}</option></select></label><button className="primary-action runtime-run" onClick={() => runInvestigation(selectedRuntime)} disabled={investigating || (selectedRuntime !== "deterministic" && !aiConfigured)}>{investigating ? "Running investigation…" : "Run investigation"}</button></div>
                <p id="runtime-help" className="runtime-help">{aiConfigured ? `Selected for the next run · ${runtimeLabels[selectedRuntime]}` : "AI investigation unavailable · Provider not configured. Deterministic remains available."}</p>
                {investigating && <p className="running" role="status">Investigation in progress. You can select another incident to cancel this view.</p>}
                {mode && !investigating && <p className="mode"><span>Mode: {mode}</span> · Last run · {runtimeLabels[mode]}</p>}
                {investigationError && <p className={isCapabilityLimitation(investigationError, selectedRuntime) ? "capability-message" : "error"} role="alert">{isCapabilityLimitation(investigationError, selectedRuntime) ? `Deterministic investigation is not supported for ${selected.catalog_id ?? "this incident"}. ${investigationError}` : investigationError}</p>}
              </section>
              <InvestigationHistory runs={investigationRuns} loading={historyLoading} selectedRunId={selectedRunId} onSelect={loadRun} />
              <InvestigationReview mode={reviewMode} run={reviewRun} status={aiMetadata?.status ?? null} result={aiResult} evidence={evidence} steps={steps} events={events} proposal={reviewingHistoricalRun ? actionProposal : null} loading={reviewLoading} error={reviewError} includeProposal={reviewingHistoricalRun} />
              {!reviewingHistoricalRun && <ExecutionPanel selected={selected} workflow={workflow} />}
            </> : <div className="empty-selection"><span aria-hidden="true">◎</span><p className="eyebrow">Evidence first. Human governed.</p><h2>Select an incident</h2><p>Choose an incident and investigation runtime to open its operator brief.</p><button type="button" className="primary-action incident-picker-trigger" onClick={openIncidentPicker}>Select incident</button></div>}
          </div>
        </div>
      </section>
      <IncidentPicker
        open={pickerOpen}
        incidents={incidents}
        candidateId={pickerIncidentId}
        runtime={pickerRuntime}
        aiConfigured={aiConfigured}
        loading={!health && !unavailable}
        unavailable={unavailable}
        onCandidateChange={setPickerIncidentId}
        onRuntimeChange={setPickerRuntime}
        onClose={() => setPickerOpen(false)}
        onSubmit={selectAndRunIncident}
      />
    </main>
  );
}
