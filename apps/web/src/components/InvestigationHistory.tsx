import type { InvestigationRun } from "../types/supportOps";
import { StatusBadge, displayStatus, formatTime } from "./supportOpsPresentation";

type Props = {
  runs: InvestigationRun[];
  loading: boolean;
  selectedRunId: number | null;
  onSelect: (run: InvestigationRun) => void;
};

function runtimeLabel(mode: string): string {
  return mode === "agents_sdk" ? "Agents SDK" : "Responses API";
}

export function InvestigationHistory({ runs, loading, selectedRunId, onSelect }: Props) {
  return (
    <section className="panel history-panel" aria-labelledby="investigation-history">
      <div className="panel-heading">
        <div>
          <p className="section-kicker">Persisted record</p>
          <h3 id="investigation-history">Investigation history</h3>
        </div>
        <span className="muted">{loading ? "Loading…" : `${runs.length} runs`}</span>
      </div>
      {runs.length === 0 && !loading ? (
        <p className="empty-state">No model investigation runs have been recorded for this incident.</p>
      ) : (
        <div className="run-history">
          {runs.map((run) => (
            <button
              className={selectedRunId === run.id ? "history-item selected" : "history-item"}
              key={run.id}
              onClick={() => onSelect(run)}
              aria-pressed={selectedRunId === run.id}
            >
              <span>
                <strong>{runtimeLabel(run.mode)}</strong>
                <small>{formatTime(run.created_at)}</small>
              </span>
              <StatusBadge status={run.status} />
              <small className="sr-only">{displayStatus(run.mode)}</small>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
