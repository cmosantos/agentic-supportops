import type { Incident } from "../types/supportOps";
import type { OperatorWorkflow } from "../hooks/useOperatorWorkflow";
import { StatusBadge, displayAction, displayStatus, formatTime } from "./supportOpsPresentation";

function certaintyLabel(certainty: string | null | undefined) {
  switch (certainty) {
    case "applied_acknowledged": return "Known successful result";
    case "not_applied": return "Known not applied";
    case "unknown": return "Outcome unknown";
    case "legacy_undetermined": return "Legacy outcome undetermined";
    default: return "Not recorded";
  }
}

function valueFromRecord(record: Record<string, unknown> | null, key: string) {
  const value = record?.[key];
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? String(value) : null;
}

function verificationResultLabel(status: string) {
  switch (status) {
    case "verified": return "Verified independently";
    case "not_verified": return "Expected state not observed";
    case "failed": return "Independent check failed";
    default: return "Verification in progress";
  }
}

function displayParameterValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value) ?? String(value);
}

type Props = {
  selected: Incident;
  workflow: Pick<OperatorWorkflow,
    "actionProposal" | "proposalError" | "decidingProposal" | "actionExecution" | "executionLookupStatus" |
    "executingAction" | "actionExecutionAttempt" | "reconciliation" | "reconciliationLookupStatus" |
    "reconciling" | "outcomeVerification" | "verifyingOutcome" | "resolutionDecisions" | "resolutionReason" |
    "decidingResolution" | "executionTimeline" | "timelineLookupStatus" | "outcomeCertainty" | "canReconcile" |
    "decideActionProposal" | "executeApprovedAction" | "reconcileExecution" | "verifyOutcome" | "decideResolution" |
    "setResolutionReason"
  >;
};

export function ExecutionPanel({ selected, workflow }: Props) {
  const {
    actionProposal, proposalError, decidingProposal, actionExecution, executionLookupStatus, executingAction,
    actionExecutionAttempt, reconciliation, reconciliationLookupStatus, reconciling, outcomeVerification,
    verifyingOutcome, resolutionDecisions, resolutionReason, decidingResolution, executionTimeline,
    timelineLookupStatus, outcomeCertainty, canReconcile, decideActionProposal, executeApprovedAction,
    reconcileExecution, verifyOutcome, decideResolution, setResolutionReason,
  } = workflow;

  return (
    <>
      {proposalError && <p className="error error-banner">{proposalError}</p>}

      {actionProposal && <section className="operator-phase action-phase" aria-labelledby="action-heading">
        <header className="phase-header">
          <div><p className="section-kicker">Proposal · Human-controlled</p><h3 id="action-heading">Proposed action</h3></div>
          <StatusBadge status={actionProposal.approval_status} />
        </header>
        <div className="action-summary">
          <div className="proposal-heading">
            <div>
              <p className="section-kicker">Decision surface</p>
              <h4>Review the exact proposed operation</h4>
            </div>
            <p>Proposal only · Nothing has been executed or changed</p>
          </div>
          <dl className="proposal-overview">
            <div className="proposal-primary"><dt>Proposed action</dt><dd>{displayAction(actionProposal.action_type)}</dd></div>
            <div><dt>Target</dt><dd>{actionProposal.target}</dd></div>
            <div><dt>Risk</dt><dd><StatusBadge status={actionProposal.risk_level} /></dd></div>
            <div><dt>Approval state</dt><dd><StatusBadge status={actionProposal.approval_status} /></dd></div>
          </dl>
          <div className="proposal-context">
            <section aria-labelledby="proposal-rationale-heading">
              <p className="section-kicker">Reason</p>
              <h5 id="proposal-rationale-heading">Why this action is proposed</h5>
              <p className="action-rationale">{actionProposal.rationale}</p>
            </section>
            <section aria-labelledby="proposal-evidence-heading">
              <p className="section-kicker">Evidence relationship</p>
              <h5 id="proposal-evidence-heading">Evidence supporting this proposal</h5>
              <p className="proposal-evidence-ids">
                {actionProposal.supporting_evidence_ids.length > 0
                  ? actionProposal.supporting_evidence_ids.map((id) => <span className="provenance-id" key={id}>#{id}</span>)
                  : "No evidence reference recorded"}
              </p>
            </section>
          </div>
          <section className="proposal-parameters" aria-labelledby="bounded-parameters-heading">
            <div className="proposal-parameters-heading">
              <div><p className="section-kicker">Approved scope</p><h5 id="bounded-parameters-heading">Bounded parameters</h5></div>
              <small>These exact values define the proposed operation.</small>
            </div>
            {Object.keys(actionProposal.parameters).length > 0 ? (
              <dl className="parameter-list">
                {Object.entries(actionProposal.parameters).map(([key, value]) => (
                  <div key={key}><dt>{displayStatus(key)}</dt><dd>{displayParameterValue(value)}</dd></div>
                ))}
              </dl>
            ) : <p className="empty-parameters">No additional parameters are recorded for this proposal.</p>}
            <details className="technical-details proposal-raw-details">
              <summary>Raw parameter JSON</summary>
              <pre>{JSON.stringify(actionProposal.parameters, null, 2)}</pre>
            </details>
          </section>
        </div>
        <section className={`governance-state governance-${actionProposal.approval_status}`} aria-labelledby="governance-decision">
          <div className="section-heading"><div><p className="section-kicker">Human approval</p><h4 id="governance-decision">A separate operator decision</h4></div><StatusBadge status={actionProposal.approval_status} /></div>
          <p><b>Approval state:</b> {displayStatus(actionProposal.approval_status)}</p>
          {actionProposal.decision_at && <p className="record-stamp">Human decision recorded · {formatTime(actionProposal.decision_at)}</p>}
          {actionProposal.rejection_reason && <p className="decision-reason"><b>Rejection reason:</b> {actionProposal.rejection_reason}</p>}
          {actionProposal.approval_status === "pending" && <p className="governance-message"><strong>Decision required.</strong> Review the exact action, target, evidence, risk, and bounded parameters before approving or rejecting it. Nothing is selected automatically.</p>}
          {actionProposal.approval_status === "approved" && !actionExecution && executionLookupStatus === "not_found" && <p className="governance-message"><strong>Approved — awaiting explicit operator execution.</strong> Approval does not start or prove execution.</p>}
          {actionProposal.approval_status === "approved" && !actionExecution && executionLookupStatus === "loading" && <p className="governance-message"><strong>Approval recorded.</strong> Persisted execution state is being checked; nothing is started automatically.</p>}
          {actionProposal.approval_status === "approved" && !actionExecution && executionLookupStatus === "error" && <p className="governance-message"><strong>Approval recorded.</strong> Execution remains a separate decision and is unavailable until persisted state can be confirmed.</p>}
          {actionProposal.approval_status === "approved" && actionExecution && <p className="governance-message"><strong>Approval was recorded before execution.</strong> The execution record is presented separately below.</p>}
          {actionProposal.approval_status === "rejected" && <p className="governance-message"><strong>Proposal rejected.</strong> Execution cannot be started from this proposal.</p>}
          {actionProposal.approval_status === "pending" && <div className="action-controls" aria-busy={decidingProposal}>
            <button className="primary-action" disabled={decidingProposal} onClick={() => decideActionProposal("approve")}>{decidingProposal ? "Recording decision…" : "Approve"}</button>
            <button disabled={decidingProposal} onClick={() => decideActionProposal("reject")}>Reject</button>
          </div>}
        </section>
        {actionProposal.approval_status === "approved" && !actionExecution && executionLookupStatus === "not_found" && <div className="next-action-control">
          <p className="human-control">Separate next step: the operator must explicitly start this exact approved action.</p>
          <button className="primary-action" disabled={executingAction} onClick={executeApprovedAction}>{executingAction ? "Execution requested…" : "Execute approved action"}</button>
        </div>}
        {actionProposal.approval_status === "approved" && executionLookupStatus === "loading" && <p role="status">Checking persisted execution…</p>}
        {actionProposal.approval_status === "approved" && executionLookupStatus === "error" && <p className="context-note">Execution controls are unavailable until persisted state can be confirmed.</p>}
      </section>}

      {actionExecution && <section className="operator-phase outcome-phase" aria-labelledby="outcome-heading">
        <header className="phase-header"><div><p className="section-kicker">Outcome</p><h3 id="outcome-heading">What happened after the action</h3></div><StatusBadge status={actionExecution.status} /></header>
        <div className="outcome-summary">
          <div className="section-heading"><div><p className="section-kicker">Controlled execution</p><h4>Execution</h4></div><StatusBadge status={actionExecution.status} /></div>
          <dl className="compact-details">
            <div><dt>Execution status:</dt><dd>{displayStatus(actionExecution.status).toUpperCase()}</dd></div>
            <div><dt>Capability:</dt><dd>{displayAction(actionExecution.capability_name)}</dd></div>
            <div><dt>Target:</dt><dd>{actionProposal?.target ?? actionExecution.result?.data?.target ?? "Not recorded"}</dd></div>
            <div><dt>Completion basis:</dt><dd>{actionExecution.completion_basis ? displayStatus(actionExecution.completion_basis) : "Not recorded"}</dd></div>
          </dl>
          <p className="record-stamp">Execution #{actionExecution.id} · Requested {formatTime(actionExecution.requested_at)} · Started {formatTime(actionExecution.started_at)}{actionExecution.completed_at ? ` · Completed ${formatTime(actionExecution.completed_at)}` : ""}</p>
          {actionExecution.result?.data && <dl className="execution-observation">
            {actionExecution.result.data.target && <><dt>Target</dt><dd>{actionExecution.result.data.target}</dd></>}
            {actionExecution.result.data.previous_state && <><dt>Previous state</dt><dd>{actionExecution.result.data.previous_state}</dd></>}
            {actionExecution.result.data.current_state && <><dt>Current state</dt><dd>{actionExecution.result.data.current_state}</dd></>}
          </dl>}
        </div>

        {actionExecutionAttempt && <article className="attempt-inline" aria-label="Physical mutation attempt">
          <div className="section-heading"><div><p className="section-kicker">Physical attempt</p><h4>Attempt #{actionExecutionAttempt.id}</h4></div><div><StatusBadge status={actionExecutionAttempt.status} /> <StatusBadge status={outcomeCertainty ?? "not_recorded"} /></div></div>
          <p><b>Attempt:</b> {actionExecutionAttempt.attempt_number} · <b>Capability:</b> {displayAction(actionExecution.capability_name)}</p>
          <p><b>Outcome certainty:</b> <span className="certainty-label">{certaintyLabel(outcomeCertainty)}</span> · <b>Started:</b> {formatTime(actionExecutionAttempt.invocation_started_at)}{actionExecutionAttempt.completed_at ? ` · Recorded ${formatTime(actionExecutionAttempt.completed_at)}` : ""}</p>
          {actionExecutionAttempt.failure_cause && <p className="attempt-failure"><b>Failure / uncertainty:</b> {actionExecutionAttempt.failure_cause}</p>}
        </article>}
        {!actionExecutionAttempt && <p className="empty-state">Physical attempt record is not available in this view.</p>}
        {actionExecution.status === "running" && <p role="status">Execution is in progress.</p>}
        {actionExecution.status === "failed" && actionExecution.error && <p className="error" role="alert">{actionExecution.error.message}</p>}
        {actionExecution.status === "outcome_unknown" && <div className="uncertainty-callout" role="alert"><strong>Outcome certainty is unknown</strong><p>The mutation may have started, so automatic retry is unsafe. The action will not be retried automatically.</p><small>Current system state must be checked before another mutation can be attempted.</small></div>}

        <details className="technical-details outcome-details">
          <summary>Operational details</summary>
          {actionExecution.result?.data && <details><summary>Technical result</summary><pre>{JSON.stringify(actionExecution.result.data, null, 2)}</pre></details>}
          <section className="execution-timeline" aria-label="Execution Timeline">
            <div className="section-heading"><div><p className="section-kicker">Audit</p><h4>Execution Timeline</h4></div><span className="count">{executionTimeline.length}</span></div>
            {timelineLookupStatus === "loading" && <p role="status">Loading execution timeline…</p>}
            {timelineLookupStatus === "error" && <p className="error" role="alert">Unable to load execution timeline.</p>}
            {timelineLookupStatus === "loaded" && executionTimeline.length === 0 && <p>No persisted lifecycle events are available for this execution.</p>}
            {timelineLookupStatus === "loaded" && executionTimeline.length > 0 && <ol className="timeline">{executionTimeline.map((entry, index) => <li key={`${entry.timestamp}-${entry.event_type}-${index}`}><span className="timeline-marker" /><div><strong>{displayStatus(entry.event_type)}</strong><small><time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time> · {entry.status ? displayStatus(entry.status).toUpperCase() : "RECORDED"}</small><small>Execution #{entry.execution_id}{entry.attempt_id ? ` · Attempt #${entry.attempt_id}` : ""}{entry.reason ? ` · Reason: ${displayStatus(entry.reason)}` : ""}</small></div></li>)}</ol>}
          </section>
        </details>
      </section>}

      {actionExecution?.status === "outcome_unknown" && reconciliationLookupStatus === "loading" && <p role="status">Checking reconciliation state…</p>}
      {canReconcile && <section className="exceptional-path" aria-label="Reconciliation control">
        <div><p className="section-kicker">Exceptional path</p><h4>Reconciliation</h4><p>The execution outcome is uncertain. Current system state must be checked before another mutation can be attempted. This is a read-only observation.</p></div>
        <button disabled={reconciling} onClick={reconcileExecution}>{reconciling ? "Reconciling state…" : "Reconcile state"}</button>
      </section>}
      {reconciliation && <section className="exceptional-path" aria-label="Reconciliation result">
        <div><p className="section-kicker">Exceptional path · Read-only discovery</p><h4>Reconciliation</h4><p>The execution outcome is uncertain. Current state was checked without retrying the original mutation. This is a read-only observation.</p></div>
        <StatusBadge status={reconciliation.status} />
        <dl className="compact-details"><div><dt>Observer:</dt><dd>{reconciliation.observer}</dd></div><div><dt>Reconciliation status:</dt><dd>{displayStatus(reconciliation.status).toUpperCase()}</dd></div>{reconciliation.expected_outcome.state && <div><dt>Expected state:</dt><dd>{reconciliation.expected_outcome.state.toUpperCase()}</dd></div>}{reconciliation.observed_outcome?.state && <div><dt>Observed state:</dt><dd>{reconciliation.observed_outcome.state.toUpperCase()}</dd></div>}</dl>
        {reconciliation.status === "desired_state_observed" && <p className="context-note">The desired technical state is currently observed. This does not prove that the original invocation succeeded.</p>}
        {reconciliation.status === "undesired_state_observed" && <p className="context-note">The desired state is not currently observed. This does not prove that the original mutation did not occur.</p>}
        {reconciliation.status === "inconclusive" && <p className="context-note">A reliable conclusion could not be obtained. The execution remains outcome unknown.</p>}
        {reconciliation.status === "running" && !reconciliation.is_stale && <p className="context-note">A reconciliation exists and is still non-terminal.</p>}
        {reconciliation.status === "running" && reconciliation.is_stale && <p className="context-note">This reconciliation appears stale. Explicit recovery is available as a separate operation and is not started here.</p>}
        {reconciliation.status === "inconclusive" && reconciliation.error && <p className="error">{reconciliation.error.message}</p>}
      </section>}

      {actionExecution?.status === "completed" && !outcomeVerification && <div className="phase-action" aria-busy={verifyingOutcome}><button className="primary-action" disabled={verifyingOutcome} onClick={verifyOutcome}>{verifyingOutcome ? "Checking observed service state…" : "Verify outcome"}</button></div>}
      {outcomeVerification && <section className="verification-section" aria-label="Outcome verification">
        <div className="section-heading"><div><p className="section-kicker">Proof</p><h4>Independent verification</h4></div><StatusBadge status={outcomeVerification.status} /></div>
        <p className="verification-result"><b>Result:</b> {verificationResultLabel(outcomeVerification.status)}</p>
        <p><b>Verification status:</b> {displayStatus(outcomeVerification.status).toUpperCase()}</p>
        <div className="verification-overview"><div><span>Expected</span><strong>{outcomeVerification.expected_outcome.state?.toUpperCase() ?? "UNKNOWN"}</strong></div><div><span>Observed</span><strong>{outcomeVerification.observed_outcome?.state?.toUpperCase() ?? "Not observed"}</strong></div><div><span>Independent read</span><strong>{valueFromRecord(outcomeVerification.evidence, "observer") ?? "Recorded verification evidence"}</strong></div></div>
        {actionExecution?.capability_name === "unlock_simulated_user" && <p className="record-stamp">Account lock state · false means unlocked</p>}
        <p className="record-stamp">Verification #{outcomeVerification.id} · {formatTime(outcomeVerification.completed_at)}</p>
        {outcomeVerification.evidence && <details><summary>Independent observation evidence</summary><pre>{JSON.stringify(outcomeVerification.evidence, null, 2)}</pre></details>}
        <p className="sr-only"><b>Expected:</b> {outcomeVerification.expected_outcome.state?.toUpperCase() ?? "UNKNOWN"}</p>
        {outcomeVerification.observed_outcome?.state && <p className="sr-only"><b>Observed:</b> {outcomeVerification.observed_outcome.state.toUpperCase()}</p>}
        {outcomeVerification.error && <p className="error">{outcomeVerification.error.message}</p>}
        {outcomeVerification.status === "not_verified" && <p className="context-note">Incident remains open because the expected outcome was not observed.</p>}
        {outcomeVerification.status === "failed" && <p className="context-note">Reliable post-execution evidence could not be collected. Incident remains open.</p>}
      </section>}
      {outcomeVerification?.status === "verified" && selected.status !== "resolved" && !resolutionDecisions.some((item) => item.verification_id === outcomeVerification.id) && <section className="closure-section" aria-label="Resolution review">
        <div className="section-heading"><div><p className="section-kicker">Closure</p><h4>Human resolution</h4></div><StatusBadge status="pending" /></div>
        <div className="closure-summary"><p><span>Finding</span><strong>Investigation and evidence are recorded.</strong></p><p><span>Action</span><strong>{actionProposal ? displayAction(actionProposal.action_type) : "Controlled action recorded"}</strong></p><p><span>Verified outcome</span><strong>Verification passed independently.</strong></p></div>
        <p className="human-control">Human resolution required. Verification does not resolve the incident automatically.</p>
        <label htmlFor="resolution-reason">Reason</label><textarea id="resolution-reason" maxLength={1000} value={resolutionReason} onChange={(event) => setResolutionReason(event.target.value)} />
        <div className="action-controls" aria-busy={decidingResolution}><button className="primary-action" disabled={decidingResolution} onClick={() => decideResolution("resolve")}>{decidingResolution ? "Recording decision…" : "Resolve incident"}</button><button disabled={decidingResolution} onClick={() => decideResolution("keep_open")}>Keep open</button></div>
      </section>}
      <p className="context-note governance-footnote">Approval permits one attempt of this exact action; execution remains policy-controlled and deterministic.</p>
      {resolutionDecisions.length > 0 && <section className="history-section resolution-history" aria-label="Resolution history"><div className="section-heading"><div><p className="section-kicker">Recorded decision</p><h3>Resolution History</h3></div><StatusBadge status={resolutionDecisions.at(-1)?.decision ?? "recorded"} /></div>{resolutionDecisions.map((decision) => <article key={decision.id}><p className="record-stamp">Human decision #{decision.id} · {formatTime(decision.decided_at)}</p><p><b>Decision:</b> {displayStatus(decision.decision).toUpperCase()}</p><p><b>Verification evidence:</b> #{decision.verification_id}</p>{decision.reason && <p><b>Reason:</b> {decision.reason}</p>}</article>)}</section>}
    </>
  );
}
