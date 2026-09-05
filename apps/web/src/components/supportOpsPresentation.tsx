export function displayStatus(status: string): string {
  return status.replaceAll("_", " ");
}

export function displayAction(action: string): string {
  const labels: Record<string, string> = {
    restart_simulated_service: "Restart simulated service",
    unlock_simulated_user: "Unlock simulated user",
    reset_simulated_application_state: "Reset simulated application state",
  };
  return labels[action] ?? displayStatus(action);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function toneFor(status: string): string {
  if (["completed", "approved", "verified", "resolved", "desired_state_observed", "applied_acknowledged"].includes(status)) return "success";
  if (["running", "investigating", "pending", "awaiting_approval"].includes(status)) return "progress";
  if (["outcome_unknown", "unknown", "not_verified", "inconclusive", "keep_open"].includes(status)) return "warning";
  if (["failed", "rejected", "undesired_state_observed"].includes(status)) return "danger";
  return "neutral";
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`status-badge ${toneFor(status)}`}>{displayStatus(status).toUpperCase()}</span>;
}

