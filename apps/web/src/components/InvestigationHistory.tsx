import type { InvestigationRun } from "../types/supportOps";
import { StatusBadge, displayStatus, formatTime } from "./supportOpsPresentation";

type Props = {
  runs: InvestigationRun[];
  loading: boolean;
  selectedRunId: number | null;
  onSelect: (run: InvestigationRun) => void;
};

function runtimeLabel(mode: string): string {
  if (mode === "deterministic") return "Deterministic";
  return mode === "agents_sdk" ? "Agents SDK" : "Responses API";
}

export function InvestigationHistory({ runs, loading, selectedRunId, onSelect }: Props) {
  return (
    <section className="history-section" aria-label="Investigation history">
      <div className="section-heading">
        <div><p className="section-kicker">Context</p><h3 id="investigation-history">Previous investigations</h3></div>
        <span className="muted">{loading ? "Loading…" : `${runs.length} run${runs.length === 1 ? "" : "s"}`}</span>
      </div>
      {runs.length === 0 && !loading ? <p className="empty-state">No previous investigations are recorded.</p> : runs.length > 0 && (
        <details className="history-disclosure" open={selectedRunId !== null}>
          <summary>View previous runs · {runs.length} recorded</summary>
          <div className="run-history">
            {runs.map((run) => (
              <button
                className={selectedRunId === run.id ? "history-item selected" : "history-item"}
                key={run.id}
                onClick={() => onSelect(run)}
                aria-pressed={selectedRunId === run.id}
              >
                <span className="history-item-primary">
                  <strong>{runtimeLabel(run.mode)}</strong>
                  <small>Run #{run.id}</small>
                  <small>{formatTime(run.created_at)}</small>
                </span>
                <span className="history-item-secondary">
                  <small className="history-selection-label">{selectedRunId === run.id ? "Selected for review" : "Historical record"}</small>
                  <StatusBadge status={run.status} />
                </span>
                <small className="sr-only">{displayStatus(run.mode)}</small>
              </button>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
