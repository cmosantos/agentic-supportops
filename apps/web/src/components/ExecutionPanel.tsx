import type { Incident } from "../types/supportOps";
import type { OperatorWorkflow } from "../hooks/useOperatorWorkflow";
import { StatusBadge, displayAction, displayStatus } from "./supportOpsPresentation";

type Props = {
  selected: Incident;
  workflow: Pick<OperatorWorkflow,
    "actionProposal" |
    "proposalError" |
    "decidingProposal" |
    "actionExecution" |
    "executionLookupStatus" |
    "executingAction" |
    "actionExecutionAttempt" |
    "reconciliation" |
    "reconciliationLookupStatus" |
    "reconciling" |
    "outcomeVerification" |
    "verifyingOutcome" |
    "resolutionDecisions" |
    "resolutionReason" |
    "decidingResolution" |
    "executionTimeline" |
    "timelineLookupStatus" |
    "outcomeCertainty" |
    "canReconcile" |
    "decideActionProposal" |
    "executeApprovedAction" |
    "reconcileExecution" |
    "verifyOutcome" |
    "decideResolution" |
    "setResolutionReason"
  >;
};

export function ExecutionPanel({ selected, workflow }: Props) {
  const {
    actionProposal,
    proposalError,
    decidingProposal,
    actionExecution,
    executionLookupStatus,
    executingAction,
    actionExecutionAttempt,
    reconciliation,
    reconciliationLookupStatus,
    reconciling,
    outcomeVerification,
    verifyingOutcome,
    resolutionDecisions,
    resolutionReason,
    decidingResolution,
    executionTimeline,
    timelineLookupStatus,
    outcomeCertainty,
    canReconcile,
    decideActionProposal,
    executeApprovedAction,
    reconcileExecution,
    verifyOutcome,
    decideResolution,
    setResolutionReason,
  } = workflow;

  return (
    <>
      {proposalError && <p className="error error-banner">{proposalError}</p>}
      {actionProposal && (
        <article className="panel proposal-panel" aria-labelledby="proposed-action">
          <div className="panel-heading">
            <div><p className="section-kicker">AI proposes · Human authorizes</p><h3 id="proposed-action">Proposed Action</h3></div>
            <StatusBadge status={actionProposal.approval_status} />
          </div>
          <p><b>Action type:</b> {displayAction(actionProposal.action_type)}</p>
          <p><b>Target:</b> {actionProposal.target}</p>
          <div className="result-section">
            <b>Bounded parameters</b>
            <pre>{JSON.stringify(actionProposal.parameters, null, 2)}</pre>
          </div>
          <p><b>Rationale:</b> {actionProposal.rationale}</p>
          <p><b>Risk level:</b> {actionProposal.risk_level}</p>
          <p><b>Supporting evidence:</b> {actionProposal.supporting_evidence_ids.map((id) => `#${id}`).join(", ")}</p>
          <p><b>Approval state:</b> {displayStatus(actionProposal.approval_status)}</p>
          {Object.keys(actionProposal.parameters).length > 0 && (
            <dl className="parameter-list">
              {Object.entries(actionProposal.parameters).map(([key, value]) => (
                <div key={key}><dt>{displayStatus(key)}</dt><dd>{String(value)}</dd></div>
              ))}
            </dl>
          )}
          {actionProposal.approval_status === "pending" && (
            <div className="actions" aria-busy={decidingProposal}>
              <button disabled={decidingProposal} onClick={() => decideActionProposal("approve")}>
                {decidingProposal ? "Recording decision…" : "Approve"}
              </button>
              <button disabled={decidingProposal} onClick={() => decideActionProposal("reject")}>Reject</button>
            </div>
          )}
          {actionProposal.approval_status === "approved" &&
            !actionExecution && executionLookupStatus === "not_found" && (
              <section className="result-section" aria-label="Approved action execution">
                <p className="human-control">Approved, awaiting explicit operator execution.</p>
                <div className="actions" aria-busy={executingAction}>
                  <button disabled={executingAction} onClick={executeApprovedAction}>
                    {executingAction ? "Execution requested…" : "Execute approved action"}
                  </button>
                </div>
              </section>
            )}
          {actionProposal.approval_status === "approved" && executionLookupStatus === "loading" && (
            <p role="status">Checking persisted execution…</p>
          )}
          {actionProposal.approval_status === "approved" && executionLookupStatus === "error" && (
            <p className="human-control">Execution controls are unavailable until persisted state can be confirmed.</p>
          )}
          {actionExecution && (
            <section className="execution-card" aria-label="Execution result">
              <div className="panel-heading"><h4>Controlled execution</h4><StatusBadge status={actionExecution.status} /></div>
              <p><b>Capability:</b> {displayAction(actionExecution.capability_name)}</p>
              <p><b>Execution status:</b> {displayStatus(actionExecution.status).toUpperCase()}</p>
              {actionExecution.completion_basis && <p><b>Completion basis:</b> {displayStatus(actionExecution.completion_basis)}</p>}
              {outcomeCertainty && (
                <div className="attempt-summary">
                  <span>Physical attempt</span><StatusBadge status={outcomeCertainty} />
                  {actionExecutionAttempt && <small>Attempt #{actionExecutionAttempt.id}</small>}
                </div>
              )}
              {actionExecution.result?.data && (
                <details><summary>Technical result</summary><pre>{JSON.stringify(actionExecution.result.data, null, 2)}</pre></details>
              )}
              {actionExecution.result?.data && (
                <dl>
                  {actionExecution.result.data.target && (
                    <><dt>Target</dt><dd>{actionExecution.result.data.target}</dd></>
                  )}
                  {actionExecution.result.data.previous_state && (
                    <><dt>Previous state</dt><dd>{actionExecution.result.data.previous_state}</dd></>
                  )}
                  {actionExecution.result.data.current_state && (
                    <><dt>Current state</dt><dd>{actionExecution.result.data.current_state}</dd></>
                  )}
                </dl>
              )}
              {actionExecution.status === "failed" && actionExecution.error && (
                <p className="error">{actionExecution.error.message}</p>
              )}
              {actionExecution.status === "outcome_unknown" && (
                <div className="unknown-outcome">
                  <strong>Outcome certainty is unknown</strong>
                  <p>The mutation may have started, so automatic retry is unsafe. The action will not be retried automatically.</p>

                </div>
              )}
            </section>
          )}
          {actionExecution?.status === "running" && (
            <p role="status">Execution is in progress.</p>
          )}
          {actionExecution && (
            <section className="result-section execution-timeline" aria-label="Execution Timeline">
              <h4>Execution Timeline</h4>
              {timelineLookupStatus === "loading" && (
                <p role="status">Loading execution timeline…</p>
              )}
              {timelineLookupStatus === "error" && (
                <p className="error" role="alert">Unable to load execution timeline.</p>
              )}
              {timelineLookupStatus === "loaded" && executionTimeline.length === 0 && (
                <p>No persisted lifecycle events are available for this execution.</p>
              )}
              {timelineLookupStatus === "loaded" && executionTimeline.length > 0 && (
                <ol>
                  {executionTimeline.map((entry, index) => (
                    <li key={`${entry.timestamp}-${entry.event_type}-${index}`} className={`timeline-${entry.status ?? "recorded"}`}>
                      <time dateTime={entry.timestamp}>{new Date(entry.timestamp).toLocaleString()}</time>
                      <p><b>{displayStatus(entry.event_type)}</b></p>
                      <p>{entry.description}</p>
                      <small>
                        Execution #{entry.execution_id}
                        {entry.attempt_id ? ` · Attempt #${entry.attempt_id}` : ""}
                        {entry.status ? ` · ${displayStatus(entry.status).toUpperCase()}` : ""}
                      </small>
                      {entry.reason && <small>Reason: {displayStatus(entry.reason)}</small>}
                    </li>
                  ))}
                </ol>
              )}
            </section>
          )}
          {actionExecution?.status === "outcome_unknown" && reconciliationLookupStatus === "loading" && (
            <p role="status">Checking reconciliation state…</p>
          )}
          {canReconcile && (
              <section className="result-section" aria-label="Reconciliation control">
                <p className="human-control">
                  Reconciliation performs a read-only observation of current state. It never retries the original mutation.
                </p>
                <div className="actions" aria-busy={reconciling}>
                  <button disabled={reconciling} onClick={reconcileExecution}>
                    {reconciling ? "Reconciling state…" : "Reconcile state"}
                  </button>
                </div>
              </section>
            )}
          {reconciliation && (
            <section className="result-section" aria-label="Reconciliation result">
              <h4>Reconciliation</h4>
              <p><b>Reconciliation status:</b> {displayStatus(reconciliation.status).toUpperCase()}</p>
              {reconciliation.expected_outcome.state && (
                <p><b>Expected state:</b> {reconciliation.expected_outcome.state.toUpperCase()}</p>
              )}
              {reconciliation.observed_outcome?.state && (
                <p><b>Observed state:</b> {reconciliation.observed_outcome.state.toUpperCase()}</p>
              )}
              {reconciliation.status === "desired_state_observed" && (
                <p className="human-control">
                  The desired technical state is currently observed. This does not prove that the original invocation succeeded.
                </p>
              )}
              {reconciliation.status === "undesired_state_observed" && (
                <p className="human-control">
                  The desired state is not currently observed. This does not prove that the original mutation did not occur.
                </p>
              )}
              {reconciliation.status === "inconclusive" && (
                <p className="human-control">
                  A reliable conclusion could not be obtained. The execution remains outcome unknown.
                </p>
              )}
              {reconciliation.status === "running" && !reconciliation.is_stale && (
                <p className="human-control">A reconciliation exists and is still non-terminal.</p>
              )}
              {reconciliation.status === "running" && reconciliation.is_stale && (
                <p className="human-control">
                  This reconciliation appears stale. Explicit recovery is available as a separate operation and is not started here.
                </p>
              )}
              {reconciliation.status === "inconclusive" && reconciliation.error && (
                <p className="error">{reconciliation.error.message}</p>
              )}
            </section>
          )}
          {actionExecution?.status === "completed" && !outcomeVerification && (
            <div className="actions" aria-busy={verifyingOutcome}>
              <button disabled={verifyingOutcome} onClick={verifyOutcome}>
                {verifyingOutcome ? "Checking observed service state…" : "Verify outcome"}
              </button>
            </div>
          )}
          {outcomeVerification && (
            <section className="result-section verification-card" aria-label="Outcome verification">
              <div className="panel-heading">
                <div><h4>Independent verification</h4><small>New read-only observation of current simulated state</small></div>
                <StatusBadge status={outcomeVerification.status} />
              </div>
              <p><b>Verification status:</b> {displayStatus(outcomeVerification.status).toUpperCase()}</p>
              <p><b>Expected:</b> {outcomeVerification.expected_outcome.state?.toUpperCase() ?? "UNKNOWN"}</p>
              {outcomeVerification.observed_outcome?.state && (
                <p><b>Observed:</b> {outcomeVerification.observed_outcome.state.toUpperCase()}</p>
              )}
              {outcomeVerification.error && (
                <p className="error">{outcomeVerification.error.message}</p>
              )}
              {outcomeVerification.status === "not_verified" && (
                <p className="human-control">Incident remains open because the expected outcome was not observed.</p>
              )}
              {outcomeVerification.status === "failed" && (
                <p className="human-control">Reliable post-execution evidence could not be collected. Incident remains open.</p>
              )}
            </section>
          )}
          {outcomeVerification?.status === "verified" &&
            selected.status !== "resolved" &&
            !resolutionDecisions.some((item) => item.verification_id === outcomeVerification.id) && (
              <section className="result-section resolution-card" aria-label="Resolution review">
                <div className="panel-heading">
                  <div><h4>Resolution Review</h4><small>Verification does not resolve the incident automatically.</small></div>
                  <StatusBadge status="open" />
                </div>
                <label htmlFor="resolution-reason">Reason</label>
                <textarea
                  id="resolution-reason"
                  maxLength={1000}
                  value={resolutionReason}
                  onChange={(event) => setResolutionReason(event.target.value)}
                />
                <div className="actions" aria-busy={decidingResolution}>
                  <button disabled={decidingResolution} onClick={() => decideResolution("resolve")}>
                    {decidingResolution ? "Recording decision…" : "Resolve incident"}
                  </button>
                  <button disabled={decidingResolution} onClick={() => decideResolution("keep_open")}>
                    Keep open
                  </button>
                </div>
              </section>
            )}
          <p className="human-control">
            Approval permits one attempt of this exact action; execution remains policy-controlled and deterministic.
          </p>
        </article>
      )}
      {resolutionDecisions.length > 0 && (
        <section className="panel" aria-label="Resolution history">
          <h3>Resolution History</h3>
          {resolutionDecisions.map((decision) => (
            <article key={decision.id}>
              <p><b>Decision:</b> {displayStatus(decision.decision).toUpperCase()}</p>
              <p><b>Verification evidence:</b> #{decision.verification_id}</p>
              {decision.reason && <p><b>Reason:</b> {decision.reason}</p>}
            </article>
          ))}
        </section>
      )}
    </>
  );
}
