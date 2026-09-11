import type {
  ActionProposal,
  AIResult,
  Evidence,
  InvestigationEvent,
  InvestigationGoal,
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
  return mode === "agents_sdk" ? "Agents SDK" : "AI";
}

function eventLabel(event: InvestigationEvent): string {
  const toolName = typeof event.metadata.tool_name === "string" ? event.metadata.tool_name : null;
  if (event.metadata.kind === "agent_delegation") return `${toolName ?? "Specialist"} · specialist delegation`;
  return toolName ?? displayStatus(event.event_type);
}

function idsForEvidence(ids: number[], evidence: Evidence[]): number[] {
  const persisted = new Set(evidence.map((item) => item.id));
  return ids.filter((id) => persisted.has(id));
}

function payloadSummary(payload: Record<string, unknown>): string {
  const entries = Object.entries(payload).slice(0, 3);
  if (entries.length === 0) return "Observation recorded without a summarized payload.";
  return entries.map(([key, value]) => {
    const readable = typeof value === "object" && value !== null ? JSON.stringify(value) : String(value);
    return `${displayStatus(key)}: ${readable}`;
  }).join(" · ");
}

function reviewContextLabel(mode: InvestigationReviewMode, run: InvestigationRun | null, includeProposal: boolean): string {
  if (run && includeProposal) return "Historical run";
  if (run) return "Selected run";
  return mode === "deterministic" ? "Persisted deterministic result" : "Current run";
}

function EvidenceReference({ label, ids, evidence }: { label: string; ids: number[]; evidence: Evidence[] }) {
  if (ids.length === 0) return <p><b>{label}:</b> None recorded</p>;
  const persisted = new Set(evidence.map((item) => item.id));
  return (
    <p>
      <b>{label}:</b>{" "}
      {ids.map((id) => <span className={persisted.has(id) ? "provenance-id" : "provenance-id missing"} key={id}>#{id}{persisted.has(id) ? "" : " · unavailable in this run"}</span>)}
    </p>
  );
}

function InvestigationContract({ goal, runId }: { goal: InvestigationGoal; runId: number }) {
  return (
    <details className="technical-details investigation-contract">
      <summary>Investigation Contract</summary>
      <p className="contract-context">
        This application-owned contract governed historical investigation run #{runId}. It is read-only audit metadata.
      </p>
      <dl className="contract-fields">
        <div className="contract-objective">
          <dt>Objective</dt>
          <dd>{goal.objective}</dd>
        </div>
        <div>
          <dt>Success Criteria</dt>
          <dd><ul>{goal.success_criteria.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></dd>
        </div>
        <div>
          <dt>Constraints</dt>
          <dd><ul>{goal.constraints.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul></dd>
        </div>
        <div className={goal.human_action_required ? "contract-approval required" : "contract-approval"}>
          <dt>Human Approval Required</dt>
          <dd>{goal.human_action_required ? "Yes" : "No"}</dd>
        </div>
      </dl>
    </details>
  );
}

export function InvestigationReview({ mode, run, status, result, evidence, steps, events, proposal, loading, error, includeProposal = true }: Props) {
  if (!mode && !loading && !error) return null;

  const scopedEvidence = run
    ? evidence.filter((item) => item.investigation_id === run.id)
    : mode === "deterministic" ? evidence.filter((item) => item.origin === "deterministic") : evidence;
  const scopedSteps = run ? steps.filter((item) => item.investigation_id === run.id) : steps;
  const scopedEvents = run ? events.filter((item) => item.investigation_id === run.id) : events;
  const proposalEvidenceIds = proposal?.supporting_evidence_ids ?? [];
  const assessmentEvidenceIds = result?.evidence_ids ?? [];
  const reviewStatus = status ?? run?.status ?? null;
  const reviewContext = mode ? reviewContextLabel(mode, run, includeProposal) : null;

  return (
    <section className="finding-section" aria-labelledby="finding-heading">
      <header className="finding-header">
        <div><p className="section-kicker">Investigation review</p><h3 id="finding-heading">What the system discovered</h3></div>
        {reviewStatus && <StatusBadge status={reviewStatus} />}
      </header>
      {loading && <p role="status">Loading investigation artifacts…</p>}
      {error && <p className="error error-banner">{error}</p>}

      {mode && <>
        <div className="finding-context" aria-label="Review context">
          <div><span>Runtime</span><strong>{runtimeLabel(mode)}</strong></div>
          {reviewStatus && <div><span>Status</span><StatusBadge status={reviewStatus} /></div>}
          {run?.model && <div><span>Model</span><strong className="technical-value">{reviewStatus ? `${reviewStatus} · ` : ""}{run.model}</strong></div>}
          {run && <div><span>Run</span><strong className="technical-value">#{run.id}</strong></div>}
          <div><span>Review</span><strong>{reviewContext}</strong></div>
        </div>
        <details className="technical-details provenance-details"><summary>Run provenance</summary>
          <dl className="detail-list">
            <div><dt>Runtime</dt><dd>{runtimeLabel(mode)} runtime</dd></div>
            {run ? <>
              <div><dt>Run</dt><dd>#{run.id}</dd></div>
              {run.model && <div><dt>Assessment</dt><dd>{status ?? run.status} · model {run.model}</dd></div>}
              <div><dt>Started</dt><dd>{formatTime(run.created_at)}</dd></div>
              <div><dt>Completed</dt><dd>{formatTime(run.completed_at)}</dd></div>
            </> : <div><dt>Record</dt><dd>Deterministic investigation record</dd></div>}
          </dl>
        </details>
        {mode === "deterministic" && <p className="context-note">Deterministic investigations do not create model assessments. This view shows the persisted playbook run, steps, and evidence.</p>}
      </>}

      {mode && <div className="finding-layout">
        <article className="finding-brief" aria-labelledby="finding-summary-heading">
          <div className="section-heading"><div><p className="section-kicker">Finding / Summary</p><h4 id="finding-summary-heading">Assessment</h4></div><span className="confidence-value">{result ? `${Math.round(result.confidence * 100)}%` : "—"}</span></div>
          {result ? <>
            <p className="finding-summary"><span>Summary</span><strong>{result.summary}</strong></p>
            <p className="finding-diagnosis"><b>Assessment:</b> {result.diagnosis}</p>
            <p className="finding-confidence"><b>Confidence:</b> {Math.round(result.confidence * 100)}%</p>
            <div className="confidence-meter" aria-label={`Confidence ${Math.round(result.confidence * 100)} percent`}><span style={{ width: `${Math.round(result.confidence * 100)}%` }} /></div>
            <EvidenceReference label="Evidence references" ids={assessmentEvidenceIds} evidence={scopedEvidence} />
            {result.supporting_evidence.length > 0 && <details><summary>Supporting observations</summary><ul>{result.supporting_evidence.map((item) => <li key={item}>{item}</li>)}</ul></details>}
            {result.missing_information.length > 0 && <details open><summary>Missing information</summary><ul>{result.missing_information.map((item) => <li key={item}>{item}</li>)}</ul></details>}
            <p className={result.human_action_required ? "human-action-required" : "human-action-neutral"}><b>Human action required:</b> {result.human_action_required ? "Yes" : "No"}</p>
          </> : <p className="empty-state">No model assessment is persisted for this investigation.</p>}
        </article>

        <article className="evidence-list" aria-labelledby="review-evidence">
          <div className="section-heading"><div><p className="section-kicker">What was observed</p><h4 id="review-evidence">Evidence</h4></div><span className="count">{scopedEvidence.length}</span></div>
          {scopedEvidence.length === 0 ? <p className="empty-state">No persisted evidence is available for this investigation.</p> : scopedEvidence.map((item) => <article className="evidence-item" key={item.id}>
            <div className="evidence-item-heading"><div><span className="evidence-label">Evidence</span><strong>#{item.id} · {item.source}</strong></div><span className="evidence-source">{item.source}</span></div>
            <dl className="evidence-facts">
              <div><dt>Source</dt><dd>{item.source}</dd></div>
              <div><dt>Target</dt><dd>{item.resource}</dd></div>
              <div><dt>Origin</dt><dd>{displayStatus(item.origin)}</dd></div>
              <div><dt>Recorded</dt><dd>{formatTime(item.created_at)}</dd></div>
            </dl>
            <p className="evidence-observation"><span>Observation / result</span>{payloadSummary(item.payload)}</p>
            {(assessmentEvidenceIds.includes(item.id) || proposalEvidenceIds.includes(item.id)) && <div className="evidence-relations" aria-label="Evidence relationships">
              <span>Supports</span>
              {assessmentEvidenceIds.includes(item.id) && <span className="provenance-id">Assessment</span>}
              {proposalEvidenceIds.includes(item.id) && <span className="provenance-id proposal-reference">Proposed action</span>}
            </div>}
            <details><summary>Observed payload</summary><pre>{JSON.stringify(item.payload, null, 2)}</pre></details>
          </article>)}
        </article>
      </div>}

      {includeProposal && <aside className="proposal-callout" aria-labelledby="review-proposal">
        <header className="proposal-header">
          <div><p className="section-kicker">Investigation output</p><h4 id="review-proposal">Proposed Action</h4></div>
          <span className="proposal-state">Proposal only</span>
        </header>
        {proposal ? <>
          <p className="proposal-note">Produced by the investigation; it has not been executed in this review.</p>
          <dl className="proposal-details">
            <div><dt>Action</dt><dd>{displayAction(proposal.action_type)}</dd></div>
            <div><dt>Target</dt><dd>{proposal.target}</dd></div>
            <div><dt>Risk</dt><dd>{proposal.risk_level}</dd></div>
            <div><dt>Approval</dt><dd>{displayStatus(proposal.approval_status)}</dd></div>
          </dl>
          <EvidenceReference label="Proposal evidence" ids={proposalEvidenceIds} evidence={scopedEvidence} />
        </> : <p className="empty-state">No action proposal was recorded for this investigation.</p>}
      </aside>}

      {run?.goal_snapshot && (
        <InvestigationContract goal={run.goal_snapshot} runId={run.id} />
      )}

      {mode && <details className="technical-details">
        <summary>Technical details · {scopedSteps.length} activity steps · {scopedEvents.length} audit events</summary>
        <div className="technical-grid">
          <article className="technical-section" aria-labelledby="review-steps">
            <div className="section-heading"><div><p className="section-kicker">Investigation activity</p><h4 id="review-steps">Investigation</h4></div><span className="count">{scopedSteps.length}</span></div>
            <p className="technical-preview">{scopedSteps[0] ? `${scopedSteps[0].tool} · ${scopedSteps[0].target_resource} · ${scopedSteps[0].status}` : "No tool activity recorded"}</p>
            <details><summary>View activity · {scopedSteps.length} steps</summary>{scopedSteps.length === 0 ? <p className="empty-state">No investigation steps are persisted for this run.</p> : <ul>{scopedSteps.map((step) => <li key={step.id}>{step.tool} · {step.target_resource} · <StatusBadge status={step.status} /></li>)}</ul>}</details>
          </article>
          <article className="technical-section" aria-labelledby="review-events">
            <div className="section-heading"><div><p className="section-kicker">Audit events</p><h4 id="review-events">Operational timeline</h4></div><span className="count">{scopedEvents.length}</span></div>
            <p className="technical-preview">{scopedEvents.length > 0 ? displayStatus(scopedEvents[scopedEvents.length - 1].event_type) : "No audit events recorded"}</p>
            <details><summary>View audit timeline · {scopedEvents.length} events</summary>{scopedEvents.length === 0 ? <p className="empty-state">No audit events are persisted for this run.</p> : <ol className="timeline">{scopedEvents.map((event) => <li key={event.id}><span className={`timeline-marker ${toneFor(event.status ?? event.event_type)}`} /><div><strong>{eventLabel(event)} · {event.status ?? "recorded"}</strong><small>{displayStatus(event.event_type)} · {formatTime(event.timestamp)}</small></div>{event.status && <StatusBadge status={event.status} />}</li>)}</ol>}</details>
          </article>
        </div>
      </details>}
    </section>
  );
}
