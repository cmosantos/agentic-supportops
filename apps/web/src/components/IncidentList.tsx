import type { Incident } from "../types/supportOps";
import { StatusBadge, displayStatus } from "./supportOpsPresentation";

type Props = {
  incidents: Incident[];
  selected: Incident | null;
  onSelect: (incident: Incident) => void;
};

export function IncidentList({ incidents, selected, onSelect }: Props) {
  return (
    <aside className="queue">
      <div className="section-heading">
        <div><p className="section-kicker">Active workload</p><h2>Incident queue</h2></div>
        <span className="count">{incidents.length}</span>
      </div>
      <div className="incident-list">
        {incidents.map((incident) => (
          <button
            className={selected?.id === incident.id ? "incident selected" : "incident"}
            key={incident.id}
            onClick={() => onSelect(incident)}
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
