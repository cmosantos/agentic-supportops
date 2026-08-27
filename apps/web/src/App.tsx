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
  source: string;
  resource: string;
  origin: InvestigationOrigin;
  payload: Record<string, unknown>;
  created_at: string;
};
type InvestigationStep = {
  id: number;
  incident_id: number;
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
  recommended_next_steps: string[];
  missing_information: string[];
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
  const [investigating, setInvestigating] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);
  const [mode, setMode] = useState<InvestigationMode | null>(null);
  const [aiConfigured, setAiConfigured] = useState(false);
  const [aiResult, setAiResult] = useState<AIResult | null>(null);
  const [aiMetadata, setAiMetadata] = useState<AIMetadata | null>(null);
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
    setAiResult(null);
    setAiMetadata(null);
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
    setAiResult(null);
    setAiMetadata(null);
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
        setAiResult(result.investigation.result);
        setAiMetadata({
          status: result.investigation.status,
          model: result.investigation.model,
        });
      } else {
        const result: Investigation = await response.json();
        if (requestVersion !== investigationVersion.current) return;
        setEvidence(result.evidence);
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
                      <strong>Validated AI result</strong>
                      {aiMetadata && (
                        <small>
                          {displayStatus(aiMetadata.status)} · {aiMetadata.model}
                        </small>
                      )}
                    </div>
                    <p>{aiResult.summary}</p>
                    <p><b>Diagnosis:</b> {aiResult.diagnosis}</p>
                    <p><b>Confidence:</b> {Math.round(aiResult.confidence * 100)}%</p>
                    {aiResult.supporting_evidence.length > 0 && (
                      <section className="result-section">
                        <b>Supporting evidence</b>
                        <ul>{aiResult.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul>
                      </section>
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
                  </article>
                )}
                {evidence.map((item) => (
                  <article key={item.id}>
                    <strong>{item.source}</strong>
                    <small>{item.resource}</small>
                    <pre>{JSON.stringify(item.payload, null, 2)}</pre>
                  </article>
                ))}
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
