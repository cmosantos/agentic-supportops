import { IncidentList } from "./components/IncidentList";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { StatusBadge, displayStatus, formatTime, toneFor } from "./components/supportOpsPresentation";
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
  const investigationRequest = useRef<AbortController | null>(null);
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
  }

  async function loadRun(run: InvestigationRun) {
    if (!selected) return;
    const version = investigationVersion.current;
    const reference = selected.catalog_id ?? selected.id;
    setHistoryLoading(true);
    workflow.setProposalError(null);
    try {
      const [artifactsResponse, eventsResponse, proposalsResponse] = await Promise.all([
        supportOpsApi.getArtifacts(reference, run.id),
        supportOpsApi.getEvents(reference, run.id),
        supportOpsApi.getProposals(reference, run.id),
      ]);
      if (!artifactsResponse.ok || !eventsResponse.ok || !proposalsResponse.ok) {
        throw new Error("Historical investigation details could not be loaded");
      }
      const artifacts: AIExecution = artifactsResponse.data;
      const historicalEvents: InvestigationEvent[] = eventsResponse.data;
      const proposals: ActionProposal[] = proposalsResponse.data;
      if (version !== investigationVersion.current) return;
      setEvidence(artifacts.evidence);
      setSteps(artifacts.steps);
      setAiResult(artifacts.investigation.result);
      setAiMetadata({ status: artifacts.investigation.status, model: artifacts.investigation.model });
      setMode(artifacts.investigation.mode === "agents_sdk" ? "agents_sdk" : "ai");
      setEvents(historicalEvents);
      workflow.showHistoricalProposal(proposals.at(-1) ?? null);
    } catch (error: unknown) {
      if (version === investigationVersion.current) {
        workflow.setProposalError(error instanceof Error ? error.message : "History loading failed");
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
          <IncidentList incidents={incidents} selected={selected} onSelect={selectIncident} />
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
                <ExecutionPanel selected={selected} workflow={workflow} />
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
