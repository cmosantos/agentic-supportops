import { useEffect, useRef } from "react";

import type { Incident } from "../types/supportOps";
import { StatusBadge, displayStatus } from "./supportOpsPresentation";

export type IncidentPickerRuntime = "deterministic" | "ai" | "agents_sdk";

type Props = {
  open: boolean;
  incidents: Incident[];
  candidateId: number | null;
  runtime: IncidentPickerRuntime | null;
  aiConfigured: boolean;
  loading?: boolean;
  unavailable?: boolean;
  onCandidateChange: (incidentId: number) => void;
  onRuntimeChange: (runtime: IncidentPickerRuntime) => void;
  onClose: () => void;
  onSubmit: () => void;
};

export function IncidentPicker({
  open,
  incidents,
  candidateId,
  runtime,
  aiConfigured,
  loading,
  unavailable,
  onCandidateChange,
  onRuntimeChange,
  onClose,
  onSubmit,
}: Props) {
  const closeButton = useRef<HTMLButtonElement>(null);
  const closeHandler = useRef(onClose);
  closeHandler.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    closeButton.current?.focus();

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") closeHandler.current();
    }

    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previouslyFocused?.focus();
    };
  }, [open]);

  if (!open) return null;

  const canSubmit = candidateId !== null && runtime !== null &&
    (runtime === "deterministic" || aiConfigured);

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="incident-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="incident-picker-title"
        aria-describedby="incident-picker-description"
      >
        <header className="dialog-header">
          <div>
            <p className="eyebrow">Investigation setup</p>
            <h2 id="incident-picker-title">Select incident</h2>
            <p id="incident-picker-description">Choose an incident and the runtime for its investigation.</p>
          </div>
          <button ref={closeButton} type="button" onClick={onClose} aria-label="Close incident picker">Close</button>
        </header>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (canSubmit) onSubmit();
          }}
        >
          <fieldset className="picker-section">
            <legend>Incident</legend>
            {loading && <p className="empty-state">Loading incidents…</p>}
            {unavailable && <p className="error error-banner" role="alert">The incident collection could not be loaded. Check backend availability and reload the console.</p>}
            {!loading && !unavailable && incidents.length === 0 && <p className="empty-state">No incidents available.</p>}
            <div className="incident-choices">
              {incidents.map((incident) => (
                <label
                  className={candidateId === incident.id ? "incident-choice selected" : "incident-choice"}
                  key={incident.id}
                >
                  <input
                    type="radio"
                    name="incident"
                    value={incident.id}
                    checked={candidateId === incident.id}
                    onChange={() => onCandidateChange(incident.id)}
                  />
                  <span className="incident-choice-content">
                    <span className="incident-topline">
                      <strong>{incident.catalog_id ?? `#${incident.id}`}</strong>
                      <StatusBadge status={incident.priority} />
                    </span>
                    <span className="incident-title">{incident.title}</span>
                    <span className="incident-meta">{incident.category} · {displayStatus(incident.status)}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="picker-section runtime-choices">
            <legend>Investigation runtime</legend>
            <label className={runtime === "deterministic" ? "runtime-choice selected" : "runtime-choice"}>
              <input type="radio" name="picker-runtime" value="deterministic" checked={runtime === "deterministic"} onChange={() => onRuntimeChange("deterministic")} />
              <span><strong>Deterministic</strong><small>Run the existing deterministic playbook.</small></span>
            </label>
            <label className={runtime === "ai" ? "runtime-choice selected" : "runtime-choice"}>
              <input type="radio" name="picker-runtime" value="ai" checked={runtime === "ai"} disabled={!aiConfigured} onChange={() => onRuntimeChange("ai")} />
              <span><strong>AI</strong><small>{aiConfigured ? "Run the configured AI investigation." : "Provider not configured."}</small></span>
            </label>
            <label className={runtime === "agents_sdk" ? "runtime-choice selected" : "runtime-choice"}>
              <input type="radio" name="picker-runtime" value="agents_sdk" checked={runtime === "agents_sdk"} disabled={!aiConfigured} onChange={() => onRuntimeChange("agents_sdk")} />
              <span><strong>Agents SDK</strong><small>{aiConfigured ? "Run the existing Agents SDK investigation." : "Provider not configured."}</small></span>
            </label>
          </fieldset>

          <footer className="dialog-actions">
            <button type="button" onClick={onClose}>Cancel</button>
            <button className="primary-action" type="submit" disabled={!canSubmit}>Select and run</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
