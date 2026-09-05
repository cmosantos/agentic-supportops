import type {
  ActionProposal,
  AIResult,
  Evidence,
  InvestigationEvent,
  InvestigationRun,
  InvestigationStep,
} from "../types/supportOps";
import { StatusBadge, displayAction, displayStatus, formatTime, toneFor } from "./supportOpsPresentation";

export type InvestigationReviewMode = "deterministic" | "ai" | "agents_sdk";

type Props = {
  mode: InvestigationReviewMode | null;
  run: InvestigationRun | null;
  status: string | null;
  result: AIResult | null;
  evidence: Evidence[];
  steps: InvestigationStep[];
  events: InvestigationEvent[];
  proposal: ActionProposal | null;
  loading: boolean;
  error: string | null;
  includeProposal?: boolean;
};

function runtimeLabel(mode: InvestigationReviewMode): string {
  if (mode === "deterministic") return "Deterministic";
  return mode === "agents_sdk" ? "Agents SDK" : "Responses API";
}

function eventLabel(event: InvestigationEvent): string {
  const toolName = typeof event.metadata.tool_name === "string" ? event.metadata.tool_name : null;
  if (event.metadata.kind === "agent_delegation") {
    return `${toolName ?? "Specialist"} · specialist delegation`;
  }
  return toolName ?? displayStatus(event.event_type);
}

function idsForEvidence(ids: number[], evidence: Evidence[]): number[] {
  const persisted = new Set(evidence.map((item) => item.id));
  return ids.filter((id) => persisted.has(id));
}

function EvidenceReference({ label, ids, evidence }: { label: string; ids: number[]; evidence: Evidence[] }) {
  if (ids.length === 0) return <p><b>{label}:</b> None recorded</p>;
  const persisted = new Set(evidence.map((item) => item.id));
  return (
    <p>
      <b>{label}:</b>{" "}
      {ids.map((id) => (
        <span className={persisted.has(id) ? "provenance-id" : "provenance-id missing"} key={id}>
          #{id}{persisted.has(id) ? "" : " · unavailable in this run"}
        </span>
      ))}
    </p>
  );
}

export function InvestigationReview({ mode, run, status, result, evidence, steps, events, proposal, loading, error, includeProposal = true }: Props) {
  if (!mode && !loading && !error) return null;

  const scopedEvidence = mode === "deterministic"
    ? evidence.filter((item) => item.origin === "deterministic" && item.investigation_id === null)
    : run
      ? evidence.filter((item) => item.investigation_id === run.id)
      : evidence;
  const scopedSteps = run ? steps.filter((item) => item.investigation_id === run.id) : steps;
  const scopedEvents = run ? events.filter((item) => item.investigation_id === run.id) : events;
  const proposalEvidenceIds = proposal?.supporting_evidence_ids ?? [];
  const assessmentEvidenceIds = result?.evidence_ids ?? [];

  return (
    <section className="investigation-review" aria-labelledby="investigation-review">
      <div className="panel review-header">
        <div className="panel-heading">
          <div>
            <p className="section-kicker">Investigation review</p>
            <h3 id="investigation-review">Run provenance</h3>
          </div>
          {status && <StatusBadge status={status} />}
        </div>
        {loading && <p role="status">Loading investigation artifacts…</p>}
        {error && <p className="error error-banner">{error}</p>}
        {mode && (
          <dl className="review-metadata">
            <div><dt>Runtime</dt><dd>{runtimeLabel(mode)}</dd></div>
            {run ? (
              <>
                <div><dt>Run</dt><dd>#{run.id}</dd></div>
                {run.model && <div><dt>Assessment</dt><dd>{status ?? run.status} · {run.model}</dd></div>}
                <div><dt>Started</dt><dd>{formatTime(run.created_at)}</dd></div>
                <div><dt>Completed</dt><dd>{formatTime(run.completed_at)}</dd></div>
              </>
            ) : (
              <div><dt>Record</dt><dd>Persisted deterministic result</dd></div>
            )}
          </dl>
        )}
        {mode === "deterministic" && (
          <p className="human-control">Deterministic investigations do not create model assessments or model run records. This view shows the persisted steps and evidence returned by the deterministic investigation.</p>
        )}
      </div>

      {mode && (
        <div className="review-grid">
          <article className="panel review-assessment">
            <div className="panel-heading"><h4>Assessment</h4><span className="count">{result ? `${Math.round(result.confidence * 100)}%` : "—"}</span></div>
            {result ? (
              <>
                <p><b>Summary:</b> {result.summary}</p>
                <p><b>Assessment:</b> {result.diagnosis}</p>
                <p><b>Diagnosis:</b> {result.diagnosis}</p>
                <p><b>Confidence:</b> {Math.round(result.confidence * 100)}%</p>
                <EvidenceReference label="Evidence references" ids={assessmentEvidenceIds} evidence={scopedEvidence} />
                {result.supporting_evidence.length > 0 && (
                  <details><summary>Supporting observations</summary><ul>{result.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul></details>
                )}
                {result.missing_information.length > 0 && (
                  <details open><summary>Missing information</summary><ul>{result.missing_information.map((item) => <li key={item}>{item}</li>)}</ul></details>
                )}
                <p><b>Human action required:</b> {result.human_action_required ? "Yes" : "No"}</p>
              </>
            ) : (
              <p className="empty-state">No model assessment is persisted for this investigation.</p>
            )}
          </article>

          <article className="panel review-evidence" aria-labelledby="review-evidence">
            <div className="panel-heading"><div><h4 id="review-evidence">Evidence</h4><small>Persisted ToolResult observations</small></div><span className="count">{scopedEvidence.length}</span></div>
            {scopedEvidence.length === 0 ? <p className="empty-state">No persisted evidence is available for this investigation.</p> : (
              scopedEvidence.map((item) => (
                <article className="provenance-item" key={item.id}>
                  <strong>#{item.id} · {item.source}</strong>
                  <small>{item.resource}</small>
                  <details><summary>Observed payload</summary><pre>{JSON.stringify(item.payload, null, 2)}</pre></details>
                </article>
              ))
            )}
          </article>

          <article className="panel review-steps" aria-labelledby="review-steps">
          <div className="panel-heading"><div><h4 id="review-steps">Investigation</h4><small>Persisted tool activity</small></div><span className="count">{scopedSteps.length}</span></div>
            {scopedSteps.length === 0 ? <p className="empty-state">No investigation steps are persisted for this run.</p> : (
              <ul>{scopedSteps.map((step) => <li key={step.id}><span className="sr-only">{step.tool} · {step.target_resource} · {step.status}</span>{step.tool} · {step.target_resource} · <StatusBadge status={step.status} /></li>)}</ul>
            )}
          </article>

          <article className="panel review-events" aria-labelledby="review-events">
          <div className="panel-heading"><div><h4 id="review-events">Operational timeline</h4><small>Safe persisted audit events</small></div><span className="count">{scopedEvents.length}</span></div>
            {scopedEvents.length === 0 ? <p className="empty-state">No audit events are persisted for this run.</p> : (
              <ol className="timeline">
                {scopedEvents.map((event) => (
                  <li key={event.id}>
                    <span className={`timeline-marker ${toneFor(event.status ?? event.event_type)}`} />
                    <div><strong>{eventLabel(event)}</strong><small>{displayStatus(event.event_type)} · {formatTime(event.timestamp)}</small></div>
                    {event.status && <StatusBadge status={event.status} />}
                  </li>
                ))}
              </ol>
            )}
          </article>

          {includeProposal && <article className="panel review-proposal" aria-labelledby="review-proposal">
            <div className="panel-heading"><div><h4 id="review-proposal">Proposed Action</h4><small>Persisted proposal provenance</small></div><span className="count">{proposal ? "1" : "0"}</span></div>
            {proposal ? (
              <>
                <p><b>Action:</b> {displayAction(proposal.action_type)}</p>
                <p><b>Target:</b> {proposal.target}</p>
                <p><b>Risk:</b> {proposal.risk_level}</p>
                <p><b>Approval:</b> {displayStatus(proposal.approval_status)}</p>
                <EvidenceReference label="Proposal evidence" ids={proposalEvidenceIds} evidence={scopedEvidence} />
              </>
            ) : <p className="empty-state">No action proposal was recorded for this investigation.</p>}
          </article>}
        </div>
      )}
    </section>
  );
}
