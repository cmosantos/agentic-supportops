import { useEffect, useState } from "react";

type Health = {
  status: string;
  service: string;
};

type Incident = {
  id: number;
  catalog_id: string | null;
  title: string;
  description: string;
  category: string;
  priority: string;
  status: string;
  affected_resource_type: string | null;
  affected_resource_id: string | null;
};

type Evidence = {
  id: number;
  source: string;
  resource: string;
  payload: Record<string, unknown>;
};

type Investigation = {
  evidence: Evidence[];
};

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selected, setSelected] = useState<Incident | null>(null);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [investigating, setInvestigating] = useState(false);
  const [investigationError, setInvestigationError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();

    fetch(`${apiBaseUrl}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed");
        return response.json() as Promise<Health>;
      })
      .then((result) => {
        setHealth(result);
        return fetch(`${apiBaseUrl}/incidents`, { signal: controller.signal });
      })
      .then((response) => {
        if (!response.ok) throw new Error("Incident list failed");
        return response.json() as Promise<Incident[]>;
      })
      .then(setIncidents)
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setUnavailable(true);
      });

    return () => controller.abort();
  }, []);

  async function runInvestigation() {
    if (!selected) return;
    setInvestigating(true);
    setInvestigationError(null);
    try {
      const reference = selected.catalog_id ?? selected.id;
      const response = await fetch(`${apiBaseUrl}/incidents/${reference}/investigate`, {
        method: "POST",
      });
      if (!response.ok) {
        const body = (await response.json()) as { detail?: { message?: string } };
        throw new Error(body.detail?.message ?? "Investigation failed");
      }
      const result = (await response.json()) as Investigation;
      setEvidence(result.evidence);
    } catch (error: unknown) {
      setInvestigationError(error instanceof Error ? error.message : "Investigation failed");
    } finally {
      setInvestigating(false);
    }
  }

  return (
    <main>
      <section className="shell">
        <p className="eyebrow">IT Support &amp; Operations</p>
        <h1>Agentic SupportOps</h1>
        <p className="summary">
          Production-oriented application baseline for incident management.
        </p>
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
                  onClick={() => {
                    setSelected(incident);
                    setEvidence([]);
                    setInvestigationError(null);
                  }}
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
                <button className="run" onClick={runInvestigation} disabled={investigating}>
                  {investigating ? "Investigating…" : "Run deterministic investigation"}
                </button>
                {investigationError && <p className="error">{investigationError}</p>}
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
