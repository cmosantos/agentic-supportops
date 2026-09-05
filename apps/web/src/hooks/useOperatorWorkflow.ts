import { useRef, useState, type RefObject } from "react";
import { supportOpsApi } from "../api/supportOpsApi";
import type {
  Incident,
  ActionProposalInput,
  ActionProposal,
  ActionExecution,
  ActionExecutionAttempt,
  Reconciliation,
  OutcomeVerification,
  IncidentResolutionDecision,
  ActionExecutionTimelineEntry,
  InvestigationEvent,
} from "../types/supportOps";

type ExecutionLookupStatus = "idle" | "loading" | "not_found" | "found" | "error";
type ReconciliationLookupStatus = "idle" | "loading" | "not_found" | "found" | "error";
type TimelineLookupStatus = "idle" | "loading" | "loaded" | "error";

function isReconciliationEligible(attempt: ActionExecutionAttempt | null): boolean {
  return Boolean(
    attempt &&
    attempt.attempt_number === 1 &&
    attempt.status === "outcome_unknown" &&
    attempt.invocation_started_at &&
    attempt.outcome_certainty === "unknown"
  );
}

type Options = {
  selected: Incident | null;
  investigationVersion: RefObject<number>;
  onEventsLoaded: (events: InvestigationEvent[]) => void;
  onIncidentResolved: (incidentId: number) => void;
};

// Coordinates persisted operational state. Audit events never identify a physical attempt.
export function useOperatorWorkflow({ selected, investigationVersion, onEventsLoaded, onIncidentResolved }: Options) {
  const [actionProposal, setActionProposal] = useState<ActionProposal | null>(null);
  const [proposalError, setProposalError] = useState<string | null>(null);
  const [decidingProposal, setDecidingProposal] = useState(false);
  const [actionExecution, setActionExecution] = useState<ActionExecution | null>(null);
  const [executionLookupStatus, setExecutionLookupStatus] = useState<ExecutionLookupStatus>("idle");
  const [executingAction, setExecutingAction] = useState(false);
  const [actionExecutionAttempt, setActionExecutionAttempt] = useState<ActionExecutionAttempt | null>(null);
  const [reconciliation, setReconciliation] = useState<Reconciliation | null>(null);
  const [reconciliationLookupStatus, setReconciliationLookupStatus] = useState<ReconciliationLookupStatus>("idle");
  const [reconciling, setReconciling] = useState(false);
  const [outcomeVerification, setOutcomeVerification] = useState<OutcomeVerification | null>(null);
  const [verifyingOutcome, setVerifyingOutcome] = useState(false);
  const [resolutionDecisions, setResolutionDecisions] = useState<IncidentResolutionDecision[]>([]);
  const [resolutionReason, setResolutionReason] = useState("");
  const [decidingResolution, setDecidingResolution] = useState(false);
  const [executionTimeline, setExecutionTimeline] = useState<ActionExecutionTimelineEntry[]>([]);
  const [timelineLookupStatus, setTimelineLookupStatus] = useState<TimelineLookupStatus>("idle");
  const proposalDecisionInFlight = useRef(false);
  const executionRequestInFlight = useRef(false);
  const reconciliationRequestInFlight = useRef(false);

  async function loadExecutionTimeline(
    executionId: number,
    signal?: AbortSignal,
    requestVersion?: number,
  ) {
    const isCurrent = () => requestVersion === undefined || requestVersion === investigationVersion.current;
    setTimelineLookupStatus("loading");
    try {
      const response = await supportOpsApi.getExecutionTimeline(executionId, signal);
      if (!isCurrent()) return;
      if (!response.ok) {
        setExecutionTimeline([]);
        setTimelineLookupStatus("error");
        return;
      }
      setExecutionTimeline(response.data);
      setTimelineLookupStatus("loaded");
    } catch {
      if (signal?.aborted || !isCurrent()) return;
      setExecutionTimeline([]);
      setTimelineLookupStatus("error");
    }
  }

  async function loadReconciliationContext(
    execution: ActionExecution,
    signal?: AbortSignal,
    requestVersion?: number,
  ) {
    const isCurrent = () => requestVersion === undefined || requestVersion === investigationVersion.current;
    setActionExecutionAttempt(null);
    setReconciliation(null);
    setReconciliationLookupStatus("idle");
    if (
      execution.status !== "outcome_unknown" &&
      execution.completion_basis !== "reconciliation"
    ) return;
    setReconciliationLookupStatus("loading");
    try {
      const attemptResponse = await supportOpsApi.getExecutionAttempt(execution.id, signal);
      if (!isCurrent()) return;
      if (!attemptResponse.ok) {
        setReconciliationLookupStatus("error");
        setProposalError(attemptResponse.error);
        return;
      }
      const attempt: ActionExecutionAttempt = attemptResponse.data;
      setActionExecutionAttempt(attempt);
      const reconciliationResponse = await supportOpsApi.getReconciliation(execution.id, attempt.id, signal);
      if (!isCurrent()) return;
      if (reconciliationResponse.status === 404) {
        setReconciliationLookupStatus("not_found");
        return;
      }
      if (!reconciliationResponse.ok) {
        setReconciliationLookupStatus("error");
        setProposalError(reconciliationResponse.error);
        return;
      }
      setReconciliation(reconciliationResponse.data);
      setReconciliationLookupStatus("found");
    } catch {
      if (signal?.aborted || !isCurrent()) return;
      setReconciliationLookupStatus("error");
      setProposalError("Unable to load persisted reconciliation state.");
    }
  }

  async function loadProposalAndExecution(proposal: ActionProposal, reference: string | number, signal: AbortSignal, requestVersion: number) {
    if (requestVersion !== investigationVersion.current) return;
    setActionProposal(proposal);
    setActionExecution(null);
    setExecutionLookupStatus("loading");
    try {
      const executionResponse = await supportOpsApi.getExecution(reference, proposal, signal);
      if (requestVersion !== investigationVersion.current) return;
      if (executionResponse.status === 404) {
        setExecutionLookupStatus("not_found");
        return;
      }
      if (!executionResponse.ok) {
        setExecutionLookupStatus("error");
        setProposalError(executionResponse.error);
        return;
      }
      const execution: ActionExecution = executionResponse.data;
      setActionExecution(execution);
      setExecutionLookupStatus("found");
      await Promise.all([
        loadReconciliationContext(execution, signal, requestVersion),
        loadExecutionTimeline(execution.id, signal, requestVersion),
      ]);
    } catch (error: unknown) {
      if (signal.aborted || requestVersion !== investigationVersion.current) return;
      setExecutionLookupStatus("error");
      setProposalError("Unable to load persisted execution state.");
    }
  }

  function reset() {
    setActionProposal(null);
    setProposalError(null);
    setActionExecution(null);
    setExecutionLookupStatus("idle");
    setExecutingAction(false);
    setActionExecutionAttempt(null);
    setReconciliation(null);
    setReconciliationLookupStatus("idle");
    setReconciling(false);
    setOutcomeVerification(null);
    setVerifyingOutcome(false);
    setResolutionDecisions([]);
    setResolutionReason("");
    setDecidingResolution(false);
    setExecutionTimeline([]);
    setTimelineLookupStatus("idle");
  }

  function loadResolutionHistory(reference: string | number, selectionVersion: number) {
    return supportOpsApi.getResolutionDecisions(reference)
      .then(async (response) => response.ok ? response.data : [])
      .then((decisions: IncidentResolutionDecision[]) => {
        if (selectionVersion === investigationVersion.current) {
          setResolutionDecisions(decisions);
        }
      })
      .catch(() => undefined);
  }

  function showHistoricalProposal(proposal: ActionProposal | null) {
    setActionProposal(proposal);
    setActionExecution(null);
    setOutcomeVerification(null);
    setReconciliation(null);
  }

  async function loadProposedAction(reference: string | number, runId: number, input: ActionProposalInput, signal: AbortSignal, requestVersion: number) {
    const persistedResponse = await supportOpsApi.getProposals(reference, runId, signal);
    if (requestVersion !== investigationVersion.current) return;
    if (!persistedResponse.ok) {
      setProposalError(persistedResponse.error);
      return;
    }
    const persistedProposals: ActionProposal[] = persistedResponse.data;
    if (persistedProposals.length > 0) {
      await loadProposalAndExecution(persistedProposals[0], reference, signal, requestVersion);
      return;
    }
    const proposalResponse = await supportOpsApi.createProposal(reference, runId, input, signal);
    if (requestVersion !== investigationVersion.current) return;
    if (!proposalResponse.ok) {
      setProposalError(proposalResponse.error);
    } else if (requestVersion === investigationVersion.current) {
      await loadProposalAndExecution(proposalResponse.data, reference, signal, requestVersion);
    }
  }

  async function decideActionProposal(decision: "approve" | "reject") {
    if (!selected || !actionProposal || proposalDecisionInFlight.current) return;
    proposalDecisionInFlight.current = true;
    setDecidingProposal(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await supportOpsApi.decideProposal(reference, actionProposal, decision);
      if (!response.ok) throw new Error(response.error);
      setActionProposal(response.data);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Proposal decision failed");
    } finally {
      proposalDecisionInFlight.current = false;
      setDecidingProposal(false);
    }
  }

  async function executeApprovedAction() {
    if (!selected || !actionProposal || executionRequestInFlight.current || actionExecution) return;
    executionRequestInFlight.current = true;
    setExecutingAction(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await supportOpsApi.executeProposal(reference, actionProposal);
      if (!response.ok) throw new Error(response.error);
      const execution: ActionExecution = response.data;
      setActionExecution(execution);
      try {
        const eventResponse = await supportOpsApi.getEvents(reference, actionProposal.investigation_id);
        if (eventResponse.ok) {
          const persistedEvents: InvestigationEvent[] = eventResponse.data;
          onEventsLoaded(persistedEvents);
        }
      } catch {
        // Execution remains authoritative even when timeline hydration is unavailable.
      }
      setExecutionLookupStatus("found");
      await Promise.all([loadReconciliationContext(execution), loadExecutionTimeline(execution.id)]);
      if (execution.status === "completed") {
        try {
          const verificationResponse = await supportOpsApi.getVerification(execution.id);
          if (verificationResponse.ok) {
            setOutcomeVerification(verificationResponse.data);
          }
        } catch {
          // Absence of prior verification leaves the explicit action available.
        }
      }
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Action execution failed");
    } finally {
      executionRequestInFlight.current = false;
      setExecutingAction(false);
    }
  }

  async function reconcileExecution() {
    if (
      !selected ||
      !actionProposal ||
      !actionExecution ||
      !actionExecutionAttempt ||
      reconciliationRequestInFlight.current
    ) return;
    reconciliationRequestInFlight.current = true;
    setReconciling(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await supportOpsApi.reconcileExecution(actionExecution.id, actionExecutionAttempt.id);
      if (!response.ok) throw new Error(response.error);
      setReconciliation(response.data);
      setReconciliationLookupStatus("found");
      const executionResponse = await supportOpsApi.getExecution(reference, actionProposal);
      if (!executionResponse.ok) throw new Error(executionResponse.error);
      const refreshedExecution: ActionExecution = executionResponse.data;
      setActionExecution(refreshedExecution);
      await loadExecutionTimeline(refreshedExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Reconciliation failed");
    } finally {
      reconciliationRequestInFlight.current = false;
      setReconciling(false);
    }
  }

  async function verifyOutcome() {
    if (
      !actionExecution ||
      actionExecution.status !== "completed" ||
      verifyingOutcome ||
      outcomeVerification
    ) return;
    setVerifyingOutcome(true);
    setProposalError(null);
    try {
      const response = await supportOpsApi.verifyExecution(actionExecution.id);
      if (!response.ok) throw new Error(response.error);
      setOutcomeVerification(response.data);
      await loadExecutionTimeline(actionExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Outcome verification failed");
    } finally {
      setVerifyingOutcome(false);
    }
  }

  async function decideResolution(decision: "resolve" | "keep_open") {
    if (
      !selected ||
      !outcomeVerification ||
      decidingResolution ||
      resolutionDecisions.some((item) => item.verification_id === outcomeVerification.id)
    ) return;
    setDecidingResolution(true);
    setProposalError(null);
    const reference = selected.catalog_id ?? selected.id;
    try {
      const response = await supportOpsApi.decideResolution(reference, { verification_id: outcomeVerification.id, decision, reason: resolutionReason.trim() || null });
      if (!response.ok) throw new Error(response.error);
      const resolution: IncidentResolutionDecision = response.data;
      setResolutionDecisions((current) =>
        current.some((item) => item.id === resolution.id) ? current : [...current, resolution]
      );
      if (resolution.decision === "resolve") {
        onIncidentResolved(resolution.incident_id);
      }
      if (actionExecution) await loadExecutionTimeline(actionExecution.id);
    } catch (error: unknown) {
      setProposalError(error instanceof Error ? error.message : "Resolution decision failed");
    } finally {
      setDecidingResolution(false);
    }
  }

  const outcomeCertainty = actionExecutionAttempt?.outcome_certainty;
  const currentResolution = outcomeVerification
    ? resolutionDecisions.find((item) => item.verification_id === outcomeVerification.id)
    : resolutionDecisions.at(-1);

  const canReconcile = actionExecution?.status === "outcome_unknown" &&
    reconciliationLookupStatus === "not_found" && isReconciliationEligible(actionExecutionAttempt);

  return {
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
    currentResolution,
    canReconcile,
    decideActionProposal,
    executeApprovedAction,
    reconcileExecution,
    verifyOutcome,
    decideResolution,
    setResolutionReason,
    reset,
    loadResolutionHistory,
    showHistoricalProposal,
    loadProposedAction,
    setProposalError,
  };
}

export type OperatorWorkflow = ReturnType<typeof useOperatorWorkflow>;
