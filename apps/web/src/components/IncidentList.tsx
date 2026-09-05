import type { Incident } from "../types/supportOps";
import { StatusBadge, displayStatus } from "./supportOpsPresentation";

type Props = {
  incidents: Incident[];
  selected: Incident | null;
  onSelect: (incident: Incident) => void;
  loading?: boolean;
  unavailable?: boolean;
};

export function IncidentList({ incidents, selected, onSelect, loading, unavailable }: Props) {
  return (
    <aside className="queue">
      <div className="section-heading">
        <div><p className="section-kicker">Operations workspace</p><h2>Incident queue</h2></div>
        <span className="count">{incidents.length}</span>
      </div>
      <p className="queue-caption">Select a case to follow its evidence and decisions.</p>
      {loading && <p className="empty-state">Loading incident queue…</p>}
      {unavailable && <p className="error error-banner" role="alert">The incident queue could not be loaded. Check backend availability and reload the console.</p>}
      {!loading && !unavailable && incidents.length === 0 && <p className="empty-state">No incidents available.</p>}
      <div className="incident-list">
        {incidents.map((incident) => (
          <button
            className={selected?.id === incident.id ? "incident selected" : "incident"}
            key={incident.id}
            onClick={() => onSelect(incident)}
            aria-pressed={selected?.id === incident.id}
          >
            <span className="incident-topline">
              <strong>{incident.catalog_id ?? `#${incident.id}`}</strong>
              <StatusBadge status={incident.priority} />
            </span>
            <span className="incident-title">{incident.title}</span>
            <span className="incident-meta">{incident.category} · {displayStatus(incident.status)}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
