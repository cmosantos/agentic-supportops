import { IncidentList } from "./components/IncidentList";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { InvestigationHistory } from "./components/InvestigationHistory";
import { InvestigationReview, type InvestigationReviewMode } from "./components/InvestigationReview";
import { StatusBadge, formatTime } from "./components/supportOpsPresentation";
import { useOperatorWorkflow } from "./hooks/useOperatorWorkflow";
import { supportOpsApi } from "./api/supportOpsApi";
import { useEffect, useRef, useState } from "react";
import type {
  Health,
  Incident,
  Evidence,
  InvestigationStep,
  AIStatus,
  AIResult,
  ActionProposal,
  InvestigationEvent,
  InvestigationRun,
  Investigation,
  AIExecution,
} from "./types/supportOps";

type InvestigationMode = "deterministic" | "ai" | "agents_sdk";
type AIMetadata = { status: AIStatus; model: string };

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
      setIncidents((current) => current.map((item) =>
        item.id === incidentId ? { ...item, status: "resolved" } : item
      ));
    },
  });
  const { actionProposal, actionExecution, outcomeVerification, currentResolution } = workflow;
  const reviewRun = selectedRunId === null
    ? null
    : investigationRuns.find((run) => run.id === selectedRunId) ?? null;
  const reviewMode: InvestigationReviewMode | null = mode === "deterministic"
    ? "deterministic"
    : mode === "agents_sdk"
      ? "agents_sdk"
      : mode === "ai"
        ? "ai"
        : null;

  useEffect(() => {
    const controller = new AbortController();

    async function loadCoreApplication() {
      try {
        const [healthResponse, incidentResponse] = await Promise.all([
          supportOpsApi.getHealth(controller.signal),
          supportOpsApi.getIncidents(controller.signal),
        ]);
        if (!healthResponse.ok || !incidentResponse.ok) {
          throw new Error("Core application data failed");
        }
        const healthResult: Health = healthResponse.data;
        const incidentResult: Incident[] = incidentResponse.data;
        setHealth(healthResult);
        setIncidents(incidentResult);
      } catch (error: unknown) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      }
    }

    async function loadAIConfiguration() {
      try {
        const response = await supportOpsApi.getAIConfiguration(controller.signal);
        if (!response.ok) return;
        const config: { configured: boolean } = response.data;
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
    reviewRequest.current?.abort();
    investigationRequest.current = null;
    reviewRequest.current = null;
    investigationVersion.current += 1;
    reviewVersion.current += 1;
    setSelected(incident);
    setEvidence([]);
    setSteps([]);
    setAiResult(null);
    setAiMetadata(null);
    setInvestigationRuns([]);
    setEvents([]);
    setHistoryLoading(true);
    setSelectedRunId(null);
    setReviewLoading(false);
    setReviewError(null);
    setReviewingHistoricalRun(false);
    workflow.reset();
    setMode(null);
    setInvestigationError(null);
    setInvestigating(false);
    const selectionVersion = investigationVersion.current;
    const reference = incident.catalog_id ?? incident.id;
    void workflow.loadResolutionHistory(reference, selectionVersion);
    void supportOpsApi.getInvestigationRuns(reference)
      .then(async (response) => response.ok ? response.data : [])
      .then((runs: InvestigationRun[]) => {
        if (selectionVersion === investigationVersion.current) {
          setInvestigationRuns(runs);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (selectionVersion === investigationVersion.current) setHistoryLoading(false);
      });
    void supportOpsApi.getInvestigation(reference)
      .then((response) => {
        if (selectionVersion !== investigationVersion.current || !response.ok) return;
        setEvidence(response.data.evidence);
        setSteps(response.data.steps);
        setAiResult(null);
        setAiMetadata(null);
        setMode("deterministic");
      })
      .catch(() => undefined);
  }

  async function loadRun(run: InvestigationRun) {
    if (!selected) return;
    reviewRequest.current?.abort();
    const controller = new AbortController();
    reviewRequest.current = controller;
    const version = investigationVersion.current;
    const runVersion = ++reviewVersion.current;
    const reference = selected.catalog_id ?? selected.id;
    setReviewLoading(true);
    setReviewError(null);
    setSelectedRunId(run.id);
    setReviewingHistoricalRun(true);
    workflow.setProposalError(null);
    try {
      const [artifactsResponse, eventsResponse, proposalsResponse] = await Promise.all([
        supportOpsApi.getArtifacts(reference, run.id, controller.signal),
        supportOpsApi.getEvents(reference, run.id, controller.signal),
        supportOpsApi.getProposals(reference, run.id, controller.signal),
      ]);
      if (!artifactsResponse.ok || !eventsResponse.ok || !proposalsResponse.ok) {
        throw new Error("Historical investigation details could not be loaded");
      }
      const artifacts: AIExecution = artifactsResponse.data;
      const historicalEvents: InvestigationEvent[] = eventsResponse.data;
      const proposals: ActionProposal[] = proposalsResponse.data;
      if (version !== investigationVersion.current || runVersion !== reviewVersion.current) return;
      setEvidence(artifacts.evidence);
      setSteps(artifacts.steps);
      setAiResult(artifacts.investigation.result);
      setAiMetadata({ status: artifacts.investigation.status, model: artifacts.investigation.model });
      setMode(artifacts.investigation.mode === "agents_sdk" ? "agents_sdk" : "ai");
      setEvents(historicalEvents);
      workflow.showHistoricalProposal(proposals.at(-1) ?? null);
    } catch (error: unknown) {
      if (!controller.signal.aborted && version === investigationVersion.current && runVersion === reviewVersion.current) {
        setReviewError(error instanceof Error ? error.message : "History loading failed");
      }
    } finally {
      if (!controller.signal.aborted && version === investigationVersion.current && runVersion === reviewVersion.current) {
        setReviewLoading(false);
      }
    }
  }

  async function runInvestigation(investigationMode: InvestigationMode) {
    if (!selected || investigationRequest.current) return;

    const controller = new AbortController();
    reviewRequest.current?.abort();
    reviewRequest.current = null;
    reviewVersion.current += 1;
    const requestVersion = ++investigationVersion.current;
    investigationRequest.current = controller;
    setInvestigating(true);
    setInvestigationError(null);
    setReviewError(null);
    setReviewingHistoricalRun(false);
    setSelectedRunId(null);
    setEvidence([]);
    setSteps([]);
    setAiResult(null);
    setAiMetadata(null);
    workflow.reset();
    setMode(investigationMode);

    try {
      const reference = selected.catalog_id ?? selected.id;
      const endpoint = investigationMode === "ai"
        ? "investigate-ai"
        : investigationMode === "agents_sdk"
          ? "investigate-agent-sdk"
          : "investigate";
      const response = await supportOpsApi.investigate<AIExecution | Investigation>(reference, endpoint, controller.signal);
      if (!response.ok) throw new Error(response.error);
      if (requestVersion !== investigationVersion.current) return;

      if (investigationMode !== "deterministic") {
        const result = response.data as AIExecution;
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
        setSteps(result.steps);
        setAiResult(result.investigation.result);
        setAiMetadata({
          status: result.investigation.status,
          model: result.investigation.model,
        });
        setSelectedRunId(result.investigation.id);
        setInvestigationRuns((current) => [
          result.investigation,
          ...current.filter((item) => item.id !== result.investigation.id),
        ]);
        void supportOpsApi.getEvents(reference, result.investigation.id, controller.signal)
          .then(async (eventResponse) => eventResponse.ok ? eventResponse.data : [])
          .then((items: InvestigationEvent[]) => {
            if (requestVersion === investigationVersion.current) setEvents(items);
          })
          .catch(() => undefined);
        if (result.investigation.result?.proposed_action) {
          await workflow.loadProposedAction(reference, result.investigation.id,
            result.investigation.result.proposed_action, controller.signal, requestVersion);
        }
      } else {
        const result = response.data as Investigation;
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
        setSteps(result.steps);
        setSelectedRunId(null);
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

  return (
    <main id="console">
      <a className="skip-link" href="#operator-workspace">Skip to operator workspace</a>
      <section className="shell">
        <header className="app-header">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">A<span>•</span></span>
            <div><p className="eyebrow">Operator Console / Governed AI Operations</p>
            <h1>Agentic SupportOps</h1>
            <p className="summary">Investigate incidents, govern remediation, and verify outcomes.</p>
            </div>
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
            <span className="system-chip">Contoso simulation</span>
            <span className={aiConfigured ? "system-chip available" : "system-chip"}>
              AI · {aiConfigured ? "available" : "not configured"}
            </span>
          </div>
        </header>
        <div className="workspace">
          <IncidentList incidents={incidents} selected={selected} onSelect={selectIncident} loading={!health && !unavailable} unavailable={unavailable} />
          <div className="details" id="operator-workspace" tabIndex={-1}>
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
                    ["Incident", selected.status, "Intake"],
                    ["Investigation", investigating ? "running" : aiMetadata?.status ?? (steps.length ? "recorded" : "not_loaded"), "Read-only"],
                    ["Evidence", evidence.length ? `${evidence.length} records` : "not_loaded", "Observation"],
                    ["Proposal", actionProposal ? "recorded" : "not_loaded", "AI proposes"],
                    ["Human Approval", actionProposal?.approval_status ?? "not_loaded", "Human governs"],
                    ["Execution", actionExecution?.status ?? "not_loaded", "Controlled"],
                    ["Attempt", !reviewingHistoricalRun ? workflow.actionExecutionAttempt?.status ?? "not_loaded" : "not_loaded", "Physical mutation"],
                    ["Verification", outcomeVerification?.status ?? "not_loaded", "Independent read"],
                    ["Human Resolution", currentResolution?.decision ?? "not_loaded", "Human decides"],
                  ].map(([label, status, owner], index) => (
                    <div className="lifecycle-step" key={label}>
                      <small>{String(index + 1).padStart(2, "0")} · {owner}</small>
                      <span>{label}</span><StatusBadge status={status} />
                    </div>
                  ))}
                </section>
                {!reviewingHistoricalRun && (actionExecution?.status === "outcome_unknown" || actionExecution?.completion_basis === "reconciliation") && (
                  <aside className="reconciliation-branch" aria-label="Uncertain outcome path">
                    <span className="branch-icon" aria-hidden="true">↳</span>
                    <div><strong>Attempt → Outcome uncertain → Reconciliation → Verification</strong>
                      <p>Independent discovery of current state. The original mutation is never repeated.</p></div>
                    <StatusBadge status={workflow.reconciliation?.status ?? "outcome_unknown"} />
                  </aside>
                )}
                <p className="lifecycle-note">Each stage has its own record. Not loaded means this view has no record to display.</p>
                <section className="panel investigation-controls" aria-labelledby="investigation-controls">
                  <div className="panel-heading">
                    <div><p className="section-kicker">Read-only investigation</p><h3 id="investigation-controls">Investigation runtimes</h3></div>
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
                <InvestigationHistory
                  runs={investigationRuns}
                  loading={historyLoading}
                  selectedRunId={selectedRunId}
                  onSelect={loadRun}
                />
                <InvestigationReview
                  mode={reviewMode}
                  run={reviewRun}
                  status={aiMetadata?.status ?? null}
                  result={aiResult}
                  evidence={evidence}
                  steps={steps}
                  events={events}
                  proposal={reviewingHistoricalRun ? actionProposal : null}
                  loading={reviewLoading}
                  error={reviewError}
                  includeProposal={reviewingHistoricalRun}
                />
                {!reviewingHistoricalRun && <ExecutionPanel selected={selected} workflow={workflow} />}
              </>
            ) : (
              <div className="empty-selection">
                <span aria-hidden="true">◎</span>
                <p className="eyebrow">Evidence first. Human governed.</p>
                <h2>Select an incident</h2>
                <p>Choose an item from the queue to review its operational lifecycle.</p>
                <div className="console-principles">
                  <div><b>01 / Investigate</b><p>Trace findings back to collected evidence.</p></div>
                  <div><b>02 / Govern</b><p>Review the proposal and authorize the exact action.</p></div>
                  <div><b>03 / Prove</b><p>Inspect execution, verify independently, then decide resolution.</p></div>
                </div>
              </div>
            )}
          </div>
        </div>
      </section>
    </main>
  );
}
