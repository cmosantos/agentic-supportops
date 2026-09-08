import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const incidents = [
  {
    id: 1,
    catalog_id: "INC-001",
    title: "Disk usage alert",
    description: "The application host is running low on disk space.",
    category: "infrastructure",
    priority: "high",
    status: "open",
    requester: "Operations",
    affected_resource_type: "host",
    affected_resource_id: "app-01",
    investigation_context: {},
    created_at: "2026-08-28T12:00:00Z",
    updated_at: "2026-08-28T12:00:00Z",
  },
  {
    id: 2,
    catalog_id: "INC-002",
    title: "DNS resolution failure",
    description: "The service name does not resolve from the application host.",
    category: "network",
    priority: "critical",
    status: "investigating",
    requester: "Support",
    affected_resource_type: "service",
    affected_resource_id: "orders.internal",
    investigation_context: {},
    created_at: "2026-08-28T12:05:00Z",
    updated_at: "2026-08-28T12:05:00Z",
  },
] as const;

const deterministicEvidence = {
  id: 10,
  incident_id: 1,
  investigation_id: null,
  source: "get_disk_usage",
  resource: "app-01",
  origin: "deterministic",
  payload: { used_percent: 94 },
  created_at: "2026-08-28T12:10:00Z",
};

const actionableExecution = {
  investigation: {
    id: 20,
    incident_id: 1,
    mode: "manual_responses",
    status: "completed",
    model: "gpt-test",
    response_id: "response-test",
    result: {
      status: "completed",
      summary: "Disk pressure confirmed.",
      diagnosis: "A controlled application reset may be appropriate.",
      confidence: 0.91,
      supporting_evidence: ["Disk usage is 94%."],
      evidence_ids: [10],
      recommended_next_steps: ["Review the proposed reset."],
      missing_information: [],
      human_action_required: true,
      proposed_action: {
        action_type: "reset_simulated_application_state",
        target: "SUPPORT-API",
        parameters: {},
        rationale: "Reset the simulated state after operator review.",
        supporting_evidence_ids: [10],
        risk_level: "medium",
      },
    },
    usage: { input_tokens: 10, output_tokens: 20, total_tokens: 30, response_iterations: 1 },
    error: null,
    created_at: "2026-08-28T12:10:00Z",
    completed_at: "2026-08-28T12:11:00Z",
  },
  evidence: [{ ...deterministicEvidence, investigation_id: 20, origin: "ai" }],
  steps: [],
};

const pendingProposal = {
  id: 40,
  investigation_id: 20,
  incident_id: 1,
  ...actionableExecution.investigation.result.proposed_action,
  approval_status: "pending",
  created_at: "2026-08-28T12:12:00Z",
  decision_at: null,
  rejection_reason: null,
};

const executableProposal = {
  ...pendingProposal,
  action_type: "restart_simulated_service",
  parameters: { service_name: "SupportApi" },
};

const approvedExecutableProposal = {
  ...executableProposal,
  approval_status: "approved",
  decision_at: "2026-08-28T12:13:00Z",
};

const completedExecution = {
  id: 50,
  proposal_id: 40,
  incident_id: 1,
  capability_name: "restart_simulated_service",
  status: "completed",
  requested_at: "2026-08-28T12:14:00Z",
  started_at: "2026-08-28T12:14:00Z",
  completed_at: "2026-08-28T12:14:01Z",
  result: {
    data: {
      target: "SUPPORT-API",
      previous_state: "degraded",
      current_state: "healthy",
      restarted: true,
    },
  },
  error: null,
  completion_basis: "acknowledged_result",
};

const outcomeUnknownExecution = {
  ...completedExecution,
  status: "outcome_unknown",
  completed_at: null,
  result: null,
  error: { code: "capability_timeout", message: "Controlled capability timed out" },
};

const canonicalUnknownAttempt = {
  id: 51,
  execution_id: 50,
  attempt_number: 1,
  status: "outcome_unknown",
  claimed_at: "2026-08-28T12:14:00Z",
  invocation_started_at: "2026-08-28T12:14:00Z",
  completed_at: "2026-08-28T12:14:01Z",
  failure_cause: "timeout",
  outcome_certainty: "unknown",
};

const reconciliationResult = {
  id: 61,
  attempt_id: 51,
  execution_id: 50,
  status: "desired_state_observed",
  observer: "get_application_health",
  expected_outcome: { state: "healthy" },
  observed_outcome: { state: "healthy" },
  evidence: null,
  error: null,
  requested_at: "2026-08-28T12:17:00Z",
  started_at: "2026-08-28T12:17:00Z",
  completed_at: "2026-08-28T12:17:01Z",
  is_stale: false,
  recoverable: false,
  recovery_block_reason: "reconciliation_not_running",
};

const verifiedOutcome = {
  id: 60,
  execution_id: 50,
  proposal_id: 40,
  incident_id: 1,
  status: "verified",
  requested_at: "2026-08-28T12:15:00Z",
  started_at: "2026-08-28T12:15:00Z",
  completed_at: "2026-08-28T12:15:01Z",
  expected_outcome: { state: "healthy" },
  observed_outcome: { state: "healthy" },
  evidence: {
    target: "SUPPORT-API",
    observer: "get_application_health",
    expected_state: "healthy",
    observed_state: "healthy",
  },
  error: null,
} as const;

const resolvedDecision = {
  id: 70,
  incident_id: 1,
  verification_id: 60,
  execution_id: 50,
  proposal_id: 40,
  decision: "resolve",
  reason: "Post-execution verification confirms service recovery.",
  decided_at: "2026-08-28T12:16:00Z",
} as const;

const executionTimeline = [
  {
    timestamp: "2026-08-28T12:14:00Z",
    event_type: "execution_requested",
    execution_id: 50,
    attempt_id: 51,
    status: "running",
    description: "Controlled execution was requested.",
    reason: null,
  },
  {
    timestamp: "2026-08-28T12:14:01Z",
    event_type: "execution_completed",
    execution_id: 50,
    attempt_id: null,
    status: "completed",
    description: "Execution reached completed state.",
    reason: "acknowledged_result",
  },
] as const;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(options?: {
  coreFailure?: boolean;
  aiConfigured?: boolean;
  incidentsOverride?: readonly unknown[];
  timeline?: (url: string, init?: RequestInit) => Promise<Response>;
  attempt?: (url: string, init?: RequestInit) => Promise<Response>;
  execution?: (url: string, init?: RequestInit) => Promise<Response>;
  proposals?: (url: string, init?: RequestInit) => Promise<Response>;
  reconciliation?: (url: string, init?: RequestInit) => Promise<Response>;
  resolution?: (url: string, init?: RequestInit) => Promise<Response>;
  get?: (url: string, init?: RequestInit) => Promise<Response | undefined>;
  post?: (url: string, init?: RequestInit) => Promise<Response>;
}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/health")) {
      return options?.coreFailure
        ? jsonResponse({ detail: "unavailable" }, 503)
        : jsonResponse({ status: "ok", service: "agentic-supportops" });
    }
    if (url.endsWith("/incidents")) return jsonResponse(options?.incidentsOverride ?? incidents);
    if (url.endsWith("/ai/config")) {
      return jsonResponse({ configured: options?.aiConfigured ?? false });
    }
    if (url.endsWith("/investigation") && !options?.get) {
      return jsonResponse({ detail: "No deterministic investigation available" }, 404);
    }
    if (!init?.method || init.method === "GET") {
      const custom = options?.get ? await options.get(url, init) : undefined;
      if (custom) return custom;
      if (url.endsWith("/investigation-runs")) return jsonResponse([]);
      if (url.endsWith("/events")) return jsonResponse([]);
    }
    if (url.endsWith("/resolution-decisions")) {
      return options?.resolution ? options.resolution(url, init) : jsonResponse([]);
    }
    if (url.endsWith("/action-proposals") && (!init?.method || init.method === "GET")) {
      return options?.proposals ? options.proposals(url, init) : jsonResponse([]);
    }
    if (url.endsWith("/execution") && (!init?.method || init.method === "GET")) {
      return options?.execution
        ? options.execution(url, init)
        : jsonResponse({ detail: { code: "action_execution_not_found", message: "Action execution not found" } }, 404);
    }
    if (url.endsWith("/timeline") && (!init?.method || init.method === "GET")) {
      return options?.timeline ? options.timeline(url, init) : jsonResponse(executionTimeline);
    }
    if (url.endsWith("/attempt") && (!init?.method || init.method === "GET")) {
      return options?.attempt ? options.attempt(url, init) : jsonResponse(canonicalUnknownAttempt);
    }
    if (url.endsWith("/reconciliation") && (!init?.method || init.method === "GET")) {
      return options?.reconciliation
        ? options.reconciliation(url, init)
        : jsonResponse({ detail: { code: "action_execution_reconciliation_not_found", message: "Reconciliation not found" } }, 404);
    }
    if (options?.post) return options.post(url, init);
    if (url.endsWith("/investigate")) {
      return jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [] });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const runtimeLabelsForTests = {
  deterministic: "Deterministic",
  ai: "AI",
  agents_sdk: "Agents SDK",
} as const;

async function selectIncident(title = "Disk usage alert") {
  const trigger = screen.queryByRole("button", { name: "Change incident" }) ??
    await screen.findByRole("button", { name: "Select incident" });
  await userEvent.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "Select incident" });
  await userEvent.click(await within(dialog).findByRole("radio", { name: new RegExp(title) }));
}

async function runInvestigation(runtime: "deterministic" | "ai" | "agents_sdk" = "ai") {
  const dialog = screen.queryByRole("dialog", { name: "Select incident" });
  if (dialog) {
    await userEvent.click(within(dialog).getByRole("radio", { name: new RegExp(`^${runtimeLabelsForTests[runtime]}`) }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Select and run" }));
    return;
  }
  await userEvent.click(await screen.findByRole("radio", { name: new RegExp(`^${runtimeLabelsForTests[runtime]}`) }));
  await userEvent.click(await screen.findByRole("button", { name: "Run investigation" }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const deterministicJourneys = [
  {
    catalog: "INC-023", target: "SUPPORT-API", title: "API health degraded",
    observations: [
      { tool: "get_application_health", resource: "SUPPORT-API", payload: { status: "degraded", error_rate_percent: 12.4 } },
      { tool: "get_host_status", resource: "API-01", payload: { status: "degraded" } },
      { tool: "get_metrics", resource: "API-01", payload: { cpu_percent: 79 } },
      { tool: "get_recent_alerts", resource: "API-01", payload: { alerts: [{ id: "ALT-002", active: true }] } },
    ],
  },
  {
    catalog: "INC-024", target: "CONTOSO-DB", title: "Database connection pool exhausted",
    observations: [
      { tool: "get_application_health", resource: "CONTOSO-DB", payload: { status: "degraded", connection_pool_percent: 98 } },
      { tool: "get_host_status", resource: "DB-01", payload: { status: "degraded" } },
      { tool: "get_metrics", resource: "DB-01", payload: { memory_percent: 92 } },
      { tool: "get_recent_alerts", resource: "DB-01", payload: { alerts: [{ id: "ALT-003", active: true }] } },
    ],
  },
  {
    catalog: "INC-026", target: "USR-FRANK", title: "User account locked",
    observations: [
      { tool: "get_user", resource: "USR-FRANK", payload: { id: "USR-FRANK", account: { enabled: true, locked: true } } },
      { tool: "get_account_status", resource: "USR-FRANK", payload: { enabled: true, locked: true } },
    ],
  },
];

function deterministicResponse(journey: typeof deterministicJourneys[number]) {
  return {
    incident_id: 1,
    catalog_id: journey.catalog,
    evidence: journey.observations.map((item, index) => ({
      ...deterministicEvidence, id: 100 + index, source: item.tool, resource: item.resource, payload: item.payload,
    })),
    steps: journey.observations.map((item, index) => ({
      id: 100 + index, incident_id: 1, investigation_id: null, origin: "deterministic",
      tool: item.tool, target_resource: item.resource, arguments: {}, status: "completed",
      result: { tool: item.tool, resource: item.resource, success: true, data: item.payload, error: null },
      created_at: "2026-08-28T12:10:00Z", completed_at: "2026-08-28T12:10:01Z",
    })),
  };
}

describe("Deterministic investigation after model history", () => {
  it("renders run-scoped evidence from the deterministic runtime contract", async () => {
    const journey = deterministicJourneys[0];
    const legacy = deterministicResponse(journey);
    const run = {
      ...actionableExecution.investigation,
      id: 30,
      mode: "deterministic",
      model: "deterministic-playbook",
      result: null,
      usage: { ...actionableExecution.investigation.usage, runtime: "deterministic" },
    };
    const response = {
      ...legacy,
      investigation: run,
      evidence: legacy.evidence.map((item) => ({ ...item, investigation_id: run.id })),
      steps: legacy.steps.map((item) => ({ ...item, investigation_id: run.id })),
    };
    const fetchMock = installFetch({
      incidentsOverride: [{ ...incidents[0], catalog_id: journey.catalog, title: journey.title }],
      post: async () => jsonResponse(response),
    });

    render(<App />);
    await selectIncident(journey.title);
    await runInvestigation("deterministic");

    expect(await screen.findByText("#100 · get_application_health")).toBeVisible();
    expect(screen.getAllByText("#30")).toHaveLength(2);
    expect(screen.getByRole("region", { name: "Investigation history" })).toHaveTextContent("Deterministic");
    expect(screen.getByText(/This view shows the persisted playbook run, steps, and evidence/)).toBeVisible();
    const currentState = screen.getByRole("region", { name: "Current operational state" });
    expect(currentState).toHaveTextContent("Evidence collected");
    expect(currentState).toHaveTextContent("Grounded evidence has been collected and is ready for operator review.");
    expect(currentState).toHaveTextContent("Review evidence");
    const lifecycle = screen.getByRole("region", { name: "Operational lifecycle" });
    const proposalStage = within(lifecycle).getByText("Proposal").closest("li");
    expect(proposalStage).toHaveTextContent("Not started");
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/action-proposals") && init?.method === "POST")).toHaveLength(0);
  });

  it.each(deterministicJourneys.flatMap((journey) =>
    (["ai", "agents_sdk"] as const).map((runtime) => ({ ...journey, runtime })),
  ))("shows only the $catalog playbook activity after reviewing $runtime", async (journey) => {
    const run = { ...actionableExecution.investigation, mode: journey.runtime };
    const response = deterministicResponse(journey);
    const fetchMock = installFetch({
      aiConfigured: true,
      incidentsOverride: [{ ...incidents[0], catalog_id: journey.catalog, title: journey.title, affected_resource_id: journey.target }],
      get: async (url) => {
        if (url.endsWith("/investigation")) return jsonResponse({ ...response, evidence: [], steps: [] });
        if (url.endsWith("/investigation-runs")) return jsonResponse([run]);
        if (url.endsWith("/artifacts")) return jsonResponse({ ...actionableExecution, investigation: run });
        if (url.endsWith("/action-proposals")) return jsonResponse([pendingProposal]);
        if (url.endsWith("/events")) return jsonResponse([{
          id: 90, investigation_id: run.id, runtime: journey.runtime === "ai" ? "manual_responses" : "agents_sdk",
          event_type: "tool_completed", sequence: 1, status: "completed",
          timestamp: "2026-08-28T12:11:00Z", metadata: { tool_name: "previous_model_tool" },
        }]);
        return undefined;
      },
      post: async (url) => url.endsWith(`/incidents/${journey.catalog}/investigate`)
        ? jsonResponse(response) : Promise.reject(new Error(`Unexpected request: ${url}`)),
    });
    render(<App />);
    await selectIncident(journey.title);
    await runInvestigation("deterministic");
    const history = screen.getByRole("region", { name: "Investigation history" });
    await userEvent.click(await within(history).findByText(/View previous runs/));
    const previousRun = within(history).getByRole("button");
    await userEvent.click(previousRun);
    expect(await screen.findByText("Disk pressure confirmed.")).toBeVisible();
    await userEvent.click(screen.getByText(/Technical details/));
    await userEvent.click(screen.getByText(/View audit timeline/));
    expect(screen.getByText("previous_model_tool · completed")).toBeVisible();

    await runInvestigation("deterministic");

    for (const [index, observation] of journey.observations.entries()) {
      expect(await screen.findByText(`#${100 + index} · ${observation.tool}`)).toBeVisible();
    }
    expect(screen.getByText(`Technical details · ${journey.observations.length} activity steps · 0 audit events`)).toBeVisible();
    expect(screen.queryByText("previous_model_tool · completed")).not.toBeInTheDocument();
    expect(screen.queryByText("Disk pressure confirmed.")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Proposed Action" })).not.toBeInTheDocument();
    expect(screen.queryByText("Loading investigation artifacts…")).not.toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /^Deterministic/ })).toBeChecked();
    expect(screen.getByRole("button", { name: "Run investigation" })).toBeEnabled();
    expect(previousRun).toHaveAttribute("aria-pressed", "false");
    expect(within(history).getAllByRole("button")).toHaveLength(1);
    expect(screen.getByRole("region", { name: "Current operational state" })).toHaveTextContent("Evidence collected");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([url]) => String(url)))
      .toEqual([
        `http://localhost:8000/incidents/${journey.catalog}/investigate`,
        `http://localhost:8000/incidents/${journey.catalog}/investigate`,
      ]);
  });

  it("finishes INC-023 deterministic review when a pending historical request is cancelled", async () => {
    const journey = deterministicJourneys[0];
    const response = deterministicResponse(journey);
    let resolveArtifacts!: (response: Response) => void;
    let historicalSignal: AbortSignal | null | undefined;
    const pendingArtifacts = new Promise<Response>((resolve) => { resolveArtifacts = resolve; });
    installFetch({
      aiConfigured: true,
      incidentsOverride: [{ ...incidents[0], catalog_id: journey.catalog, title: journey.title }],
      get: async (url, init) => {
        if (url.endsWith("/investigation")) return jsonResponse({ ...response, steps: [], evidence: [] });
        if (url.endsWith("/investigation-runs")) return jsonResponse([actionableExecution.investigation]);
        if (url.endsWith("/artifacts")) { historicalSignal = init?.signal; return pendingArtifacts; }
        return undefined;
      },
      post: async () => jsonResponse(response),
    });
    render(<App />);
    await selectIncident(journey.title);
    await runInvestigation("deterministic");
    const history = screen.getByRole("region", { name: "Investigation history" });
    await userEvent.click(await within(history).findByText(/View previous runs/));
    await userEvent.click(within(history).getByRole("button"));
    expect(await screen.findByText("Loading investigation artifacts…")).toBeVisible();
    await runInvestigation("deterministic");
    expect(await screen.findByText("#100 · get_application_health")).toBeVisible();
    expect(historicalSignal?.aborted).toBe(true);
    expect(screen.queryByText("Loading investigation artifacts…")).not.toBeInTheDocument();
    await act(async () => { resolveArtifacts(jsonResponse(actionableExecution)); });
    expect(screen.getByText("#100 · get_application_health")).toBeVisible();
    expect(screen.queryByText("Disk pressure confirmed.")).not.toBeInTheDocument();
  });
});

describe("Operator console golden journeys", () => {
  it.each([
    { catalog: "INC-023", target: "SUPPORT-API", title: "API degraded", action: "restart_simulated_service", parameters: { service_name: "SupportApi" }, sources: ["get_application_health"], observer: "get_application_health", state: "healthy" },
    { catalog: "INC-024", target: "CONTOSO-DB", title: "Connection pool exhausted", action: "reset_simulated_application_state", parameters: {}, sources: ["get_application_health", "get_host_status", "get_metrics", "get_recent_alerts"], observer: "get_application_health", state: "healthy" },
    { catalog: "INC-026", target: "USR-FRANK", title: "Account locked", action: "unlock_simulated_user", parameters: {}, sources: ["get_user", "get_account_status"], observer: "get_account_status", state: "false" },
  ])("presents $catalog from evidence through explicit human resolution", async (journey) => {
    const incident = { ...incidents[0], catalog_id: journey.catalog, title: journey.title, affected_resource_id: journey.target };
    const evidence = journey.sources.map((source, index) => ({
      ...deterministicEvidence, id: 10 + index, source, resource: journey.target,
      investigation_id: 20, origin: "ai", payload: { state: journey.state === "false" ? "locked" : "degraded" },
    }));
    const proposal = { ...pendingProposal, action_type: journey.action, target: journey.target, parameters: journey.parameters, supporting_evidence_ids: evidence.map(item => item.id) };
    const execution = { ...completedExecution, capability_name: journey.action, result: { data: { target: journey.target, previous_state: "degraded", current_state: journey.state } } };
    const verification = { ...verifiedOutcome, expected_outcome: { state: journey.state }, observed_outcome: { state: journey.state }, evidence: { observer: journey.observer, target: journey.target, observed_state: journey.state } };
    const fetchMock = installFetch({
      aiConfigured: true, incidentsOverride: [incident],
      attempt: async () => jsonResponse({ ...canonicalUnknownAttempt, status: "completed", outcome_certainty: "applied_acknowledged", failure_cause: null }),
      resolution: async (_url, init) => jsonResponse(init?.method === "POST" ? resolvedDecision : []),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse({ ...actionableExecution, evidence, investigation: { ...actionableExecution.investigation, result: { ...actionableExecution.investigation.result, evidence_ids: evidence.map(item => item.id), proposed_action: proposal } } });
        if (url.endsWith("/action-proposals")) return jsonResponse(proposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...proposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(execution);
        if (url.endsWith("/verification")) return jsonResponse({}, 404);
        if (url.endsWith("/verify")) return jsonResponse(verification);
        throw new Error(`Unexpected golden journey request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident(journey.title);
    await runInvestigation("ai");
    await screen.findByRole("button", { name: "Approve" });
    for (const [index, source] of journey.sources.entries()) {
      expect(screen.getByText(`#${10 + index} · ${source}`)).toBeVisible();
    }
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));
    expect(await screen.findByText("APPLIED ACKNOWLEDGED")).toBeVisible();
    expect(screen.queryByRole("complementary", { name: "Uncertain outcome path" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reconcile state" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/reconciliation"))).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Resolve incident" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Verify outcome" }));
    await userEvent.click(await screen.findByText("Independent observation evidence"));
    const observation = screen.getByRole("region", { name: "Outcome verification" });
    expect(observation).toHaveTextContent(journey.observer);
    expect(observation).toHaveTextContent(journey.target);
    expect(within(observation).getByText("Observed:").closest("p")).toHaveTextContent(journey.state.toUpperCase());
    expect(screen.getByText("Incident status:").closest("p")).toHaveTextContent("OPEN");
    await userEvent.click(screen.getByRole("button", { name: "Resolve incident" }));
    await waitFor(() => expect(screen.getByText("Incident status:").closest("p")).toHaveTextContent("RESOLVED"));
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/verify"))).toHaveLength(1);
  });
});

describe("Agentic SupportOps operator workflow", () => {
  it("shows loading before rendering incidents and backend health", async () => {
    installFetch();
    render(<App />);

    expect(screen.getByText("Checking backend health…")).toBeInTheDocument();
    expect(screen.queryByRole("complementary", { name: "Incident queue" })).not.toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Select incident" })).toBeVisible();
    expect(screen.queryByText("Disk usage alert")).not.toBeInTheDocument();
    expect(screen.getByText("Backend online — agentic-supportops")).toBeVisible();
  });

  it("shows the existing unavailable state when core loading fails", async () => {
    installFetch({ coreFailure: true });
    render(<App />);

    expect(await screen.findByText("Backend unavailable")).toBeVisible();
    expect(screen.queryByRole("button", { name: /INC-001/ })).not.toBeInTheDocument();
  });

  it("opens an accessible incident picker and closes it with Escape", async () => {
    installFetch();
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "Select incident" }));
    const dialog = screen.getByRole("dialog", { name: "Select incident" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByRole("radio", { name: /INC-001.*Disk usage alert/ })).toBeVisible();
    expect(within(dialog).getByRole("button", { name: "Select and run" })).toBeDisabled();

    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Select incident" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select incident" })).toHaveFocus();
  });

  it("keeps AI and Agents SDK unavailable when AI is not configured", async () => {
    installFetch();
    render(<App />);

    await userEvent.click(await screen.findByRole("button", { name: "Select incident" }));
    const dialog = screen.getByRole("dialog", { name: "Select incident" });
    expect(within(dialog).getByRole("radio", { name: /^AI/ })).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: /^Agents SDK/ })).toBeDisabled();
    expect(within(dialog).getByRole("radio", { name: /^Deterministic/ })).toBeEnabled();
  });

  it("keeps unavailable AI runtimes visible in the active runtime selector", async () => {
    installFetch();
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");

    expect(screen.getByRole("radio", { name: /^AI Investigation/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Agents SDK/ })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Deterministic/ })).toBeEnabled();
    expect(screen.getByText(/AI runtimes unavailable · Provider not configured/)).toBeVisible();
  });

  it("does not start a new investigation when only the runtime is selected", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate")) return jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [] });
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error("Unexpected request: " + url);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);

    const aiRuntime = screen.getByRole("radio", { name: /^AI Investigation/ });
    await userEvent.click(aiRuntime);
    expect(aiRuntime).toBeChecked();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);

    await userEvent.click(screen.getByRole("button", { name: "Run investigation" }));
    await screen.findByText("Disk pressure confirmed.");
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/investigate-ai"))).toHaveLength(1);
  });

  it("reopens the picker from the active incident without starting on incident click", async () => {
    const fetchMock = installFetch();
    render(<App />);
    await selectIncident();
    await runInvestigation("deterministic");

    await userEvent.click(await screen.findByRole("button", { name: "Change incident" }));
    const dialog = screen.getByRole("dialog", { name: "Select incident" });
    expect(within(dialog).getByRole("radio", { name: /INC-001.*Disk usage alert/ })).toBeChecked();
    expect(within(dialog).getByRole("button", { name: "Select and run" })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);

    await userEvent.click(within(dialog).getByRole("radio", { name: /INC-002.*DNS resolution failure/ }));
    expect(screen.getByRole("heading", { name: "Disk usage alert" })).toBeVisible();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("keeps active incident metadata and lifecycle state visible", async () => {
    installFetch({
      post: async (url) => url.endsWith("/investigate")
        ? jsonResponse({
            incident_id: 1,
            catalog_id: "INC-001",
            evidence: [deterministicEvidence],
            steps: [{
              id: 30,
              incident_id: 1,
              investigation_id: null,
              tool: "get_disk_usage",
              target_resource: "app-01",
              origin: "deterministic",
              arguments: {},
              status: "completed",
              result: {},
              created_at: "2026-08-28T12:10:00Z",
              completed_at: "2026-08-28T12:10:01Z",
            }],
          })
        : Promise.reject(new Error(`Unexpected request: ${url}`)),
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("deterministic");

    const title = await screen.findByRole("heading", { name: "Disk usage alert" });
    const header = title.closest("header");
    expect(header).not.toBeNull();
    const incidentHeader = within(header as HTMLElement);
    expect(incidentHeader.getByText("INC-001")).toBeVisible();
    expect(incidentHeader.getByText("Incident status:").closest("p")).toHaveTextContent("OPEN");
    expect(incidentHeader.getByText("Severity").closest("div")).toHaveTextContent("HIGH");
    expect(incidentHeader.getByText("Affected resource").closest("div")).toHaveTextContent("app-01");
    expect(incidentHeader.getByText("Category").closest("div")).toHaveTextContent("infrastructure");
    expect(incidentHeader.getByText("Updated")).toBeVisible();
    expect(incidentHeader.getByText("The application host is running low on disk space.")).toBeVisible();
    expect(incidentHeader.getByRole("button", { name: "Change incident" })).toBeVisible();

    const lifecycle = screen.getByRole("region", { name: "Operational lifecycle" });
    const stages = within(lifecycle).getAllByRole("listitem");
    expect(stages).toHaveLength(9);
    expect(within(stages[0]).getByText("Incident")).toBeVisible();
    expect(within(stages[0]).getByText("open")).toBeVisible();
    expect(within(stages[1]).getByText("Investigation")).toBeVisible();
    expect(within(stages[1]).getByText("recorded")).toBeVisible();
    expect(within(stages[2]).getByText("Evidence")).toBeVisible();
    expect(within(stages[2]).getByText("1 records")).toBeVisible();
    expect(within(stages[3]).getByText("Proposal")).toBeVisible();
    expect(within(stages[3]).getByText("Not started")).toBeVisible();
    expect(within(lifecycle).queryByText("Reconciliation")).not.toBeInTheDocument();
  });

  it("selects incidents and clears investigation output from the previous selection", async () => {
    const fetchMock = installFetch({
      post: async (url) => jsonResponse({
        incident_id: url.includes("INC-001") ? 1 : 2,
        catalog_id: url.includes("INC-001") ? "INC-001" : "INC-002",
        steps: [],
        evidence: url.includes("INC-001") ? [deterministicEvidence] : [],
      }),
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    await userEvent.click(await screen.findByText("Observed payload"));
    expect(await screen.findByText(/"used_percent": 94/)).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/incidents/INC-001/investigate"))).toBe(true);
    expect(screen.getByRole("radio", { name: /^Deterministic/ })).toBeChecked();

    await selectIncident("DNS resolution failure");
    await runInvestigation("deterministic");
    expect(screen.getByRole("heading", { name: "DNS resolution failure" })).toBeVisible();
    expect(screen.queryByText(/"used_percent": 94/)).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Current operational state" })).toHaveTextContent("Awaiting investigation");
    expect(screen.getByText("Mode: deterministic")).toBeVisible();
  });

  it("disables controls while a deterministic investigation runs and renders its evidence", async () => {
    let resolveInvestigation!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveInvestigation = resolve;
    });
    installFetch({ post: async () => pending });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    expect(screen.getByRole("button", { name: "Running investigation…" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: /^Deterministic/ })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Investigation in progress");

    resolveInvestigation(
      jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [deterministicEvidence] }),
    );
    await userEvent.click(await screen.findByText("Observed payload"));
    expect(await screen.findByText(/"used_percent": 94/)).toBeVisible();
    expect(screen.getByText("Mode: deterministic")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run investigation" })).toBeEnabled();
  });

  it("surfaces a structured investigation conflict without leaking protocol details", async () => {
    installFetch({
      post: async () => jsonResponse({ detail: { code: "run_conflict", message: "Investigation already running" } }, 409),
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");

    expect(await screen.findByText("Investigation already running")).toBeVisible();
    expect(screen.getByText("Mode: deterministic")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run investigation" })).toBeEnabled();
  });

  it("distinguishes a configured AI investigation and renders its validated result", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async () =>
        jsonResponse({
          investigation: {
            id: 20,
            incident_id: 1,
            mode: "manual_responses",
            status: "completed",
            model: "gpt-test",
            response_id: "response-test",
            result: {
              status: "completed",
              summary: "Disk pressure confirmed.",
              diagnosis: "Logs are consuming the volume.",
              confidence: 0.91,
              supporting_evidence: ["Disk usage is 94%."],
              evidence_ids: [10],
              recommended_next_steps: ["Review log retention."],
              missing_information: ["Log growth rate is not available."],
              human_action_required: true,
            },
            usage: { input_tokens: 10, output_tokens: 20, total_tokens: 30, response_iterations: 1 },
            error: null,
            created_at: "2026-08-28T12:10:00Z",
            completed_at: "2026-08-28T12:11:00Z",
          },
          evidence: [{ ...deterministicEvidence, investigation_id: 20, origin: "ai" }],
          steps: [{
            id: 30,
            incident_id: 1,
            investigation_id: 20,
            tool: "get_disk_usage",
            target_resource: "app-01",
            origin: "ai",
            arguments: { device_id: "app-01" },
            status: "completed",
            result: {},
            created_at: "2026-08-28T12:10:00Z",
            completed_at: "2026-08-28T12:10:01Z",
          }],
        }),
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(screen.getByText(/Technical details/));

    expect(await screen.findByText("Disk pressure confirmed.")).toBeVisible();
    expect(screen.getByText("Assessment:").closest("p")).toHaveTextContent(
      "Assessment: Logs are consuming the volume.",
    );
    expect(screen.getByText("Confidence:").closest("p")).toHaveTextContent("Confidence: 91%");
    expect(screen.getByText("completed · gpt-test")).toBeVisible();
    expect(screen.getByText("Mode: ai")).toBeVisible();
    expect(screen.getByText("Evidence references:").closest("p")).toHaveTextContent("#10");
    expect(screen.getByRole("heading", { name: "Investigation" })).toBeVisible();
    expect(screen.getByText(/get_disk_usage · app-01 · completed/)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Evidence" })).toBeVisible();
    expect(screen.getByText("Log growth rate is not available.")).toBeVisible();
    expect(screen.getByText(/Human action required/)).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/incidents/INC-001/investigate-ai"))).toBe(true);
    expect(screen.getByRole("region", { name: "Current operational state" })).toHaveTextContent("Review findings");
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/action-proposals") && init?.method === "POST")).toHaveLength(0);
    expect(screen.getByRole("radio", { name: /^AI Investigation/ })).toBeChecked();
  });

  it("does not let an older investigation response overwrite a newly selected incident", async () => {
    let resolveOldRequest!: (response: Response) => void;
    const oldRequest = new Promise<Response>((resolve) => {
      resolveOldRequest = resolve;
    });
    installFetch({
      post: async (url) => url.includes("INC-001")
        ? oldRequest
        : jsonResponse({ incident_id: 2, catalog_id: "INC-002", steps: [], evidence: [] }),
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    await selectIncident("DNS resolution failure");
    await runInvestigation("deterministic");
    resolveOldRequest(
      jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [deterministicEvidence] }),
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "DNS resolution failure" })).toBeVisible();
      expect(screen.queryByText(/"used_percent": 94/)).not.toBeInTheDocument();
      expect(screen.getByText("Mode: deterministic")).toBeVisible();
    });
  });

  it("surfaces Agents SDK as an alternative runtime over the same incident", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-agent-sdk")) {
          return jsonResponse({
            ...actionableExecution,
            investigation: {
              ...actionableExecution.investigation,
              mode: "agents_sdk",
              result: { ...actionableExecution.investigation.result, proposed_action: null },
            },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("agents_sdk");

    expect(await screen.findByText("Mode: agents_sdk")).toBeVisible();
    expect(fetchMock.mock.calls.some(([url]) =>
      String(url).endsWith("/investigate-agent-sdk")
    )).toBe(true);
    expect(screen.getByRole("region", { name: "Current operational state" })).toHaveTextContent("Review findings");
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/action-proposals") && init?.method === "POST")).toHaveLength(0);
    expect(screen.getByRole("radio", { name: /^Agents SDK/ })).toBeChecked();
  });

  it("starts the selected incident and runtime only from the explicit picker action", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => url.endsWith("/investigate-agent-sdk")
        ? jsonResponse({ ...actionableExecution, investigation: { ...actionableExecution.investigation, mode: "agents_sdk", result: { ...actionableExecution.investigation.result, proposed_action: null } } })
        : Promise.reject(new Error(`Unexpected request: ${url}`)),
    });
    render(<App />);

    await selectIncident();
    const dialog = screen.getByRole("dialog", { name: "Select incident" });
    const run = within(dialog).getByRole("button", { name: "Select and run" });
    expect(run).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(0);

    const agentsRuntime = within(dialog).getByRole("radio", { name: /^Agents SDK/ });
    await userEvent.click(agentsRuntime);
    expect(agentsRuntime).toBeChecked();
    expect(run).toBeEnabled();
    await userEvent.click(run);

    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/investigate-agent-sdk"))).toBe(true);
    expect(screen.queryByRole("dialog", { name: "Select incident" })).not.toBeInTheDocument();
  });

  it("loads persisted investigation history as readable artifacts and timeline", async () => {
    installFetch({
      aiConfigured: true,
      get: async (url) => {
        if (url.endsWith("/investigation-runs")) {
          return jsonResponse([actionableExecution.investigation]);
        }
        if (url.endsWith("/artifacts")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse([pendingProposal]);
        if (url.endsWith("/events")) {
          return jsonResponse([{
            id: 90,
            investigation_id: 20,
            runtime: "manual_responses",
            event_type: "run_completed",
            sequence: 1,
            status: "completed",
            timestamp: "2026-08-28T12:11:00Z",
            metadata: {},
          }]);
        }
        return undefined;
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    await userEvent.click(screen.getByText(/View previous runs/));
    await userEvent.click(await screen.findByRole("button", { name: /Responses API/ }));
    await userEvent.click(screen.getByText(/Technical details/));

    expect(await screen.findByRole("heading", { name: "Operational timeline" })).toBeVisible();
    expect(screen.getByText("run completed")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Proposed Action" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Evidence" })).toBeVisible();
  });

  it("keeps evidence and proposals isolated when the operator switches between runs", async () => {
    const secondRun = { ...actionableExecution.investigation, id: 21, mode: "agents_sdk" as const, response_id: "agents-response" };
    const secondEvidence = { ...deterministicEvidence, id: 11, investigation_id: 21, origin: "agents_sdk" };
    installFetch({
      aiConfigured: true,
      get: async (url) => {
        if (url.endsWith("/investigation-runs")) return jsonResponse([actionableExecution.investigation, secondRun]);
        if (url.includes("/investigation-runs/20/artifacts")) return jsonResponse(actionableExecution);
        if (url.includes("/investigation-runs/21/artifacts")) return jsonResponse({ ...actionableExecution, investigation: secondRun, evidence: [secondEvidence] });
        if (url.endsWith("/action-proposals")) return jsonResponse([]);
        if (url.endsWith("/events")) return jsonResponse([]);
        return undefined;
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    const history = screen.getByRole("region", { name: "Investigation history" });
    await userEvent.click(within(history).getByText(/View previous runs/));
    const runButtons = within(history).getAllByRole("button");
    await userEvent.click(runButtons[0]);
    expect(await screen.findByText("#10 · get_disk_usage")).toBeVisible();
    await userEvent.click(runButtons[1]);
    expect(await screen.findByText("#11 · get_disk_usage")).toBeVisible();
    expect(screen.queryByText("#10 · get_disk_usage")).not.toBeInTheDocument();
    expect(screen.getAllByText("Agents SDK").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByText(/Technical details/));
    expect(screen.getByText("No action proposal was recorded for this investigation.")).toBeVisible();
  });

  it("represents deterministic history without inventing a model assessment", async () => {
    installFetch({
      post: async (url) => url.endsWith("/investigate")
        ? jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [{ id: 1, incident_id: 1, investigation_id: null, tool: "get_disk_usage", target_resource: "app-01", status: "completed", evidence_ids: [10], started_at: "2026-08-28T12:10:00Z", completed_at: "2026-08-28T12:10:01Z", error: null }], evidence: [deterministicEvidence] })
        : Promise.reject(new Error(`Unexpected request: ${url}`)),
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    expect(await screen.findByText("Persisted deterministic result")).toBeVisible();
    expect(screen.getByRole("radio", { name: /^Deterministic/ })).toBeChecked();
    expect(screen.getByText("#10 · get_disk_usage")).toBeVisible();
    expect(screen.getByText("No model assessment is persisted for this investigation.")).toBeVisible();
  });

  it("shows a review error without exposing a stale run after artifact loading fails", async () => {
    installFetch({
      aiConfigured: true,
      get: async (url) => {
        if (url.endsWith("/investigation-runs")) return jsonResponse([actionableExecution.investigation]);
        if (url.endsWith("/artifacts")) return jsonResponse({ detail: "Artifacts unavailable" }, 503);
        if (url.endsWith("/events") || url.endsWith("/action-proposals")) return jsonResponse([]);
        return undefined;
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("deterministic");
    await userEvent.click(await screen.findByRole("button", { name: /Responses API/ }));
    expect(await screen.findByText("Historical investigation details could not be loaded")).toBeVisible();
    expect(screen.queryByText("#10 · get_disk_usage")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve|Reject|Execute/ })).not.toBeInTheDocument();
  });

  it.each([
    ["Approve", "approved"],
    ["Reject", "rejected"],
  ])("renders a pending proposal and records %s without execution", async (decision, status) => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(pendingProposal, 201);
        if (url.endsWith(`/${decision.toLowerCase()}`)) {
          return jsonResponse({
            ...pendingProposal,
            approval_status: status,
            decision_at: "2026-08-28T12:13:00Z",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByRole("heading", { name: "Proposed action" })).toBeVisible();
    const proposalSurface = screen.getByRole("heading", { name: "Review the exact proposed operation" }).closest(".action-summary");
    expect(proposalSurface).not.toBeNull();
    const proposal = within(proposalSurface as HTMLElement);
    expect(proposal.getByText("Proposed action", { selector: "dt" }).closest("div")).toHaveTextContent("Reset simulated application state");
    expect(proposal.getByText("Target", { selector: "dt" }).closest("div")).toHaveTextContent("SUPPORT-API");
    expect(proposal.getByText("Risk", { selector: "dt" }).closest("div")).toHaveTextContent("MEDIUM");
    expect(screen.getByRole("heading", { name: "Evidence supporting this proposal" }).parentElement).toHaveTextContent("#10");
    expect(screen.getByText("Approval state:").closest("p")).toHaveTextContent("pending");
    expect(screen.getByRole("region", { name: "Current operational state" })).toHaveTextContent("Approve proposed action");
    expect(screen.getByRole("button", { name: "Approve" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Reject" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: decision }));

    await waitFor(() => {
      expect(screen.getByText("Approval state:").closest("p")).toHaveTextContent(status);
    });
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reject" })).not.toBeInTheDocument();
    expect(screen.getByText(/execution remains policy-controlled/)).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith(`/${decision.toLowerCase()}`))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
    if (status === "approved") {
      expect(screen.getByText(/Approved — awaiting explicit operator execution/)).toBeVisible();
      expect(screen.getByRole("button", { name: "Execute approved action" })).toBeVisible();
    } else {
      expect(screen.getByText("Proposal rejected.", { selector: "strong" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Execute approved action" })).not.toBeInTheDocument();
    }
  });

  it("renders an already persisted proposal without recreating it", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([executableProposal]),
      post: async (url, init) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByRole("heading", { name: "Proposed action" })).toBeVisible();
    expect(screen.getByText("service name", { selector: "dt" }).closest("div")).toHaveTextContent("SupportApi");
    expect(screen.getByRole("heading", { name: "Evidence supporting this proposal" }).parentElement).toHaveTextContent("#10");
    expect(screen.getByText("Raw parameter JSON").parentElement).toHaveTextContent('"service_name": "SupportApi"');
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("surfaces proposal decision API failures and keeps the proposal pending", async () => {
    installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(pendingProposal, 201);
        if (url.endsWith("/approve")) {
          return jsonResponse({ detail: { code: "proposal_already_decided", message: "Proposal already decided" } }, 409);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText("Proposal already decided")).toBeVisible();
    expect(screen.getByText("Approval state:").closest("p")).toHaveTextContent("pending");
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
  });

  it("submits only one proposal decision while the request is in flight", async () => {
    let resolveDecision!: (response: Response) => void;
    const decisionRequest = new Promise<Response>((resolve) => {
      resolveDecision = resolve;
    });
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(pendingProposal, 201);
        if (url.endsWith("/approve")) return decisionRequest;
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);

    await selectIncident();
    await runInvestigation("ai");
    const approve = await screen.findByRole("button", { name: "Approve" });
    approve.click();
    approve.click();

    expect(await screen.findByRole("button", { name: "Recording decision…" })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/approve"))).toHaveLength(1);
    resolveDecision(jsonResponse({ ...pendingProposal, approval_status: "approved" }));
    await waitFor(() => expect(screen.getByText("Approval state:").closest("p")).toHaveTextContent("approved"));
  });

  it("executes an approved proposal once, shows loading, and renders COMPLETED", async () => {
    let resolveExecution!: (response: Response) => void;
    const executionRequest = new Promise<Response>((resolve) => {
      resolveExecution = resolve;
    });
    const fetchMock = installFetch({
      aiConfigured: true,
      attempt: async () => jsonResponse({
        ...canonicalUnknownAttempt,
        status: "completed",
        failure_cause: null,
        outcome_certainty: "applied_acknowledged",
      }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) {
          return jsonResponse({ ...executableProposal, approval_status: "approved" });
        }
        if (url.endsWith("/execute")) return executionRequest;
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(screen.queryByRole("button", { name: /execute approved/i })).not.toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));
    expect(screen.getByRole("button", { name: "Execution requested…" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Verify outcome" })).not.toBeInTheDocument();

    resolveExecution(jsonResponse(completedExecution));
    const controlledExecution = await screen.findByRole("region", { name: "Controlled execution" });
    expect(within(controlledExecution).getByText("Execution status:").closest("div")).toHaveTextContent("COMPLETED");
    expect(within(controlledExecution).getByText("Capability:").closest("div")).toHaveTextContent("Restart simulated service");
    expect(within(controlledExecution).getByText("Target:").closest("div")).toHaveTextContent("SUPPORT-API");
    expect(within(controlledExecution).getByText("Previous state").closest("div")).toHaveTextContent("degraded");
    expect(within(controlledExecution).getByText("Current state").closest("div")).toHaveTextContent("healthy");
    expect(await screen.findByRole("article", { name: "Physical attempt #51" })).toHaveTextContent("Known successful result");
    await userEvent.click(screen.getByText("Operational details"));
    await userEvent.click(screen.getByText("Technical result"));
    expect(screen.getByText(/"current_state": "healthy"/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /execute/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Verify outcome" })).toBeVisible();
    const executeCalls = fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"));
    expect(executeCalls).toHaveLength(1);
    expect(executeCalls[0][1]).toEqual({ method: "POST" });
  });

  it("does not execute automatically after approval", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));

    expect(await screen.findByText(/Approved — awaiting explicit operator execution/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Execute approved action" })).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
  });

  it("submits only one explicit execution request while it is in flight", async () => {
    let resolveExecution!: (response: Response) => void;
    const executionRequest = new Promise<Response>((resolve) => { resolveExecution = resolve; });
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return executionRequest;
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    const execute = await screen.findByRole("button", { name: "Execute approved action" });
    execute.click();
    execute.click();

    expect(await screen.findByRole("button", { name: "Execution requested…" })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(1);
    resolveExecution(jsonResponse(completedExecution));
    await screen.findByText("Execution status:");
  });

  it("renders OUTCOME_UNKNOWN distinctly without retry or verification controls", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse({
          ...completedExecution,
          status: "outcome_unknown",
          completed_at: null,
          result: null,
          error: { code: "capability_timeout", message: "Controlled capability timed out with an unknown outcome" },
        });
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("OUTCOME UNKNOWN");
    expect(await screen.findByRole("article", { name: "Physical attempt #51" })).toHaveTextContent("Outcome unknown");
    expect(screen.getByText(/cannot safely determine whether the requested change occurred/i)).toBeVisible();
    expect(screen.getByText(/neither a confirmed success nor a confirmed failure/i)).toBeVisible();
    expect(screen.getByText(/will not be retried automatically/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /retry|execute approved|verify outcome/i })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(1);
  });

  it("renders a canonical RUNNING execution without sending another request", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse({
          ...completedExecution,
          status: "running",
          completed_at: null,
          result: null,
        });
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("RUNNING");
    expect(screen.getByText(/execution is in progress/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /execute approved/i })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(1);
  });

  it("shows Execute when persisted lookup confirms an approved proposal has no execution", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(
        { detail: { code: "action_execution_not_found", message: "Action execution not found" } },
        404,
      ),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByRole("button", { name: "Execute approved action" })).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execution"))).toHaveLength(1);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
  });

  it("rehydrates a persisted COMPLETED execution without calling execute", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(completedExecution),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("COMPLETED");
    const observedState = screen.getAllByText("Current state").find((element) => element.tagName === "DT");
    expect(observedState?.nextElementSibling).toHaveTextContent("healthy");
    expect(screen.queryByRole("button", { name: "Execute approved action" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
  });

  it("renders the execution timeline in API order with attempt and reason", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(completedExecution),
      post: async (url) => url.endsWith("/investigate-ai")
        ? jsonResponse(actionableExecution)
        : Promise.reject(new Error(`Unexpected request: ${url}`)),
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    await userEvent.click(screen.getByText("Operational details"));
    const timeline = await screen.findByRole("region", { name: "Execution Timeline" });
    const items = within(timeline).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("execution requested");
    expect(items[0]).toHaveTextContent("Attempt #51");
    expect(items[1]).toHaveTextContent("execution completed");
    expect(items[1]).toHaveTextContent("Reason: acknowledged result");
  });

  it("renders empty and error states for the execution timeline", async () => {
    const timeline = vi.fn()
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ detail: "unavailable" }, 503));
    const fetchOptions = {
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(completedExecution),
      timeline,
      post: async (url: string) => url.endsWith("/investigate-ai")
        ? jsonResponse(actionableExecution)
        : Promise.reject(new Error(`Unexpected request: ${url}`)),
    };

    installFetch(fetchOptions);
    const first = render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(screen.getByText("Operational details"));
    expect(await screen.findByText(/No persisted lifecycle events/)).toBeVisible();
    first.unmount();

    installFetch(fetchOptions);
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(screen.getByText("Operational details"));
    expect(await screen.findByText("Unable to load execution timeline.")).toBeVisible();
  });

  it("rehydrates a persisted FAILED execution", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse({
        ...completedExecution,
        status: "failed",
        result: null,
        error: { code: "application_not_found", message: "Application not found" },
      }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("FAILED");
    expect(screen.getByText("Application not found")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Execute approved action" })).not.toBeInTheDocument();
  });

  it("rehydrates persisted OUTCOME_UNKNOWN without retry control", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse({
        ...completedExecution,
        status: "outcome_unknown",
        completed_at: null,
        result: null,
        error: { code: "capability_timeout", message: "Controlled capability timed out" },
      }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("OUTCOME UNKNOWN");
    expect(screen.getByText(/will not be retried automatically/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /retry|execute approved/i })).not.toBeInTheDocument();
  });

  it("surfaces unexpected execution lookup failure and keeps Execute disabled", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(
        { detail: { code: "execution_lookup_failed", message: "Execution state is temporarily unavailable" } },
        503,
      ),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByText("Execution state is temporarily unavailable")).toBeVisible();
    expect(screen.getByText(/controls are unavailable until persisted state can be confirmed/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Execute approved action" })).not.toBeInTheDocument();
    expect(screen.queryByText("Execution status:")).not.toBeInTheDocument();
  });

  it("shows Reconcile only for an eligible OUTCOME_UNKNOWN attempt and never starts it automatically", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByRole("button", { name: "Reconcile state" })).toBeVisible();
    expect(screen.getByText(/read-only observation/i)).toBeVisible();
    expect(fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/reconcile") && init?.method === "POST")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /retry|execute approved/i })).not.toBeInTheDocument();
  });

  it("does not expose Reconcile for an ineligible physical attempt", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      attempt: async () => jsonResponse({ ...canonicalUnknownAttempt, status: "failed", outcome_certainty: "not_applied" }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await screen.findByText("Execution status:");

    expect(screen.queryByRole("button", { name: "Reconcile state" })).not.toBeInTheDocument();
  });

  it.each([404, 503])("does not use audit attempt IDs when canonical attempt lookup returns %s", async (status) => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      attempt: async () => jsonResponse({ detail: "Canonical attempt unavailable" }, status),
      timeline: async () => jsonResponse(executionTimeline),
      get: async (url) => url.endsWith("/events") ? jsonResponse([{
        id: 91, investigation_id: 20, runtime: "manual_responses",
        event_type: "execution_attempt_outcome_unknown", sequence: 5,
        status: "outcome_unknown", timestamp: "2026-08-28T12:14:02Z",
        metadata: { attempt_id: 77, outcome_certainty: "unknown" },
      }]) : undefined,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByText("Canonical attempt unavailable")).toBeVisible();
    await userEvent.click(screen.getByText("Operational details"));
    expect(await screen.findByRole("region", { name: "Execution Timeline" })).toHaveTextContent("Attempt #51");
    expect(screen.queryByRole("button", { name: /reconcile|retry|execute approved|verify outcome/i })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/attempts/"))).toBe(false);
  });

  it("ignores a canonical attempt response after the operator selects another incident", async () => {
    let finishAttempt!: (response: Response) => void;
    const pendingAttempt = new Promise<Response>((resolve) => { finishAttempt = resolve; });
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      attempt: async () => pendingAttempt,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/investigate")) {
          return jsonResponse({ incident_id: 2, catalog_id: "INC-002", steps: [], evidence: [] });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await screen.findByText("Checking reconciliation state…");
    await selectIncident("DNS resolution failure");
    await runInvestigation("deterministic");
    finishAttempt(jsonResponse(canonicalUnknownAttempt));
    await waitFor(() => expect(screen.queryByText("Execution status:")).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Reconcile state" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/attempts/"))).toBe(false);
  });

  it("submits one explicit reconciliation request despite duplicate clicks", async () => {
    let resolveReconciliation!: (response: Response) => void;
    const pendingReconciliation = new Promise<Response>((resolve) => { resolveReconciliation = resolve; });
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/reconcile")) return pendingReconciliation;
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    const reconcile = await screen.findByRole("button", { name: "Reconcile state" });
    reconcile.click();
    reconcile.click();

    expect(await screen.findByRole("button", { name: "Reconciling state…" })).toBeDisabled();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/reconcile"))).toHaveLength(1);
    resolveReconciliation(jsonResponse(reconciliationResult));
    expect((await screen.findByText("Reconciliation status:")).closest("div")).toHaveTextContent("DESIRED STATE OBSERVED");
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/timeline"))).toHaveLength(2);
    });
  });

  it.each([
    ["desired_state_observed", "healthy", /does not prove that the original invocation succeeded/i],
    ["undesired_state_observed", "degraded", /does not prove that the original mutation did not occur/i],
    ["inconclusive", null, /reliable conclusion could not be obtained/i],
    ["running", null, /still non-terminal/i],
  ] as const)("rehydrates canonical reconciliation %s instead of creating another", async (status, observed, explanation) => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(
        status === "desired_state_observed"
          ? { ...completedExecution, completion_basis: "reconciliation" }
          : outcomeUnknownExecution,
      ),
      reconciliation: async () => jsonResponse({
        ...reconciliationResult,
        status,
        observed_outcome: observed ? { state: observed } : null,
        error: status === "inconclusive" ? { code: "observer_failure", message: "Reliable evidence unavailable" } : null,
        completed_at: status === "running" ? null : reconciliationResult.completed_at,
        recovery_block_reason: status === "running" ? "not_stale" : "reconciliation_not_running",
      }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect((await screen.findByText("Reconciliation status:")).closest("div")).toHaveTextContent(status.replaceAll("_", " ").toUpperCase());
    expect(screen.getByText(explanation)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Reconcile state" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/reconcile"))).toHaveLength(0);
  });

  it("shows stale RUNNING reconciliation without recovery or polling", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      reconciliation: async () => jsonResponse({
        ...reconciliationResult,
        status: "running",
        observed_outcome: null,
        completed_at: null,
        is_stale: true,
        recoverable: true,
        recovery_block_reason: null,
      }),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");

    expect(await screen.findByText(/reconciliation appears stale/i)).toBeVisible();
    expect(screen.getByText(/explicit recovery is available as a separate operation/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /recover/i })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/recover"))).toHaveLength(0);
  });

  it("keeps OUTCOME_UNKNOWN safe when reconciliation API fails", async () => {
    installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(outcomeUnknownExecution),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/reconcile")) {
          return jsonResponse({ detail: { code: "execution_reconciliation_conflict", message: "Reconciliation unavailable" } }, 409);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Reconcile state" }));

    expect(await screen.findByText("Reconciliation unavailable")).toBeVisible();
    expect(screen.getByText("Execution status:").closest("div")).toHaveTextContent("OUTCOME UNKNOWN");
    expect(screen.queryByRole("button", { name: /retry|execute approved/i })).not.toBeInTheDocument();
  });

  it("refreshes canonical execution after desired state reconciliation", async () => {
    let executionReads = 0;
    const fetchMock = installFetch({
      aiConfigured: true,
      proposals: async () => jsonResponse([approvedExecutableProposal]),
      execution: async () => jsonResponse(++executionReads === 1 ? outcomeUnknownExecution : completedExecution),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/reconcile")) return jsonResponse(reconciliationResult);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Reconcile state" }));

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("COMPLETED");
    expect(screen.getByText("Reconciliation status:").closest("div")).toHaveTextContent("DESIRED STATE OBSERVED");
    expect(executionReads).toBe(2);
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/execute"))).toHaveLength(0);
  });

  it("shows an execution API error without inventing execution state", async () => {
    installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) {
          return jsonResponse({ detail: { code: "execution_policy_denied", message: "Execution is not eligible" } }, 403);
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect(await screen.findByText("Execution is not eligible")).toBeVisible();
    expect(screen.queryByText("Execution status:")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Execute approved action" })).toBeEnabled();
  });

  it("renders a controlled FAILED execution and its safe error", async () => {
    installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) {
          return jsonResponse({ ...executableProposal, approval_status: "approved" });
        }
        if (url.endsWith("/execute")) {
          return jsonResponse({
            ...completedExecution,
            status: "failed",
            result: null,
            error: { code: "application_not_found", message: "Application not found" },
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect((await screen.findByText("Execution status:")).closest("div")).toHaveTextContent("FAILED");
    expect(screen.getByText("Application not found")).toBeVisible();
    expect(screen.queryByRole("button", { name: /execute approved/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Verify outcome" })).not.toBeInTheDocument();
  });

  it("presents OUTCOME_UNKNOWN as unsafe to retry and offers governed reconciliation", async () => {
    const unknownExecution = {
      ...completedExecution,
      status: "outcome_unknown",
      completed_at: null,
      result: null,
      completion_basis: null,
      error: { code: "capability_outcome_unknown", message: "Outcome could not be acknowledged" },
    };
    const fetchMock = installFetch({
      aiConfigured: true,
      get: async (url) => {
        if (url.endsWith("/events")) {
          return jsonResponse([{
            id: 91,
            investigation_id: 20,
            runtime: "manual_responses",
            event_type: "execution_attempt_outcome_unknown",
            sequence: 5,
            status: "outcome_unknown",
            timestamp: "2026-08-28T12:14:02Z",
            metadata: { attempt_id: 77, outcome_certainty: "unknown" },
          }]);
        }
        return undefined;
      },
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(unknownExecution);
        if (url.endsWith("/reconcile")) {
          return jsonResponse({
            id: 80,
            attempt_id: 51,
            execution_id: 50,
            status: "desired_state_observed",
            observer: "get_application_health",
            expected_outcome: { state: "healthy" },
            observed_outcome: { state: "healthy" },
            error: null,
            requested_at: "2026-08-28T12:15:00Z",
            started_at: "2026-08-28T12:15:00Z",
            completed_at: "2026-08-28T12:15:01Z",
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect(await screen.findByText("Outcome certainty is unknown")).toBeVisible();
    expect(screen.getByText(/automatic retry is unsafe/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /execute approved/i })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reconcile state" }));
    expect(await screen.findByRole("region", { name: "Reconciliation result" })).toHaveTextContent(
      "DESIRED STATE OBSERVED",
    );
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/attempts/51/reconcile"))).toHaveLength(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/attempts/77/"))).toBe(false);
  });

  it.each([
    ["verified", "healthy", null],
    ["not_verified", "degraded", null],
    ["failed", null, "Unable to collect reliable post-execution evidence."],
  ] as const)("verifies a completed execution and renders %s distinctly", async (status, observed, error) => {
    let resolveVerification!: (response: Response) => void;
    const verificationRequest = new Promise<Response>((resolve) => {
      resolveVerification = resolve;
    });
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(completedExecution);
        if (url.endsWith("/verification")) return jsonResponse({}, 404);
        if (url.endsWith("/verify")) return verificationRequest;
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));
    await userEvent.click(await screen.findByRole("button", { name: "Verify outcome" }));

    expect(screen.getByRole("button", { name: "Checking observed service state…" })).toBeDisabled();
    resolveVerification(jsonResponse({
      ...verifiedOutcome,
      status,
      observed_outcome: observed ? { state: observed } : null,
      evidence: observed ? { ...verifiedOutcome.evidence, observed_state: observed } : null,
      error: error ? { code: "observer_failure", message: error } : null,
    }));

    expect((await screen.findByText("Verification status:")).closest("p")).toHaveTextContent(
      status.replace("_", " ").toUpperCase(),
    );
    if (observed) {
      expect(screen.getByText("Observed:").closest("p")).toHaveTextContent(observed.toUpperCase());
    }
    if (error) expect(screen.getByText(error)).toBeVisible();
    if (status === "verified") {
      expect(screen.getByRole("button", { name: "Resolve incident" })).toBeVisible();
      expect(screen.getByRole("button", { name: "Keep open" })).toBeVisible();
    } else {
      expect(screen.queryByRole("button", { name: "Resolve incident" })).not.toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: "Verify outcome" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/verify"))).toHaveLength(1);
  });

  it("hydrates an existing canonical verification and cannot trigger it again", async () => {
    const fetchMock = installFetch({
      aiConfigured: true,
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(completedExecution);
        if (url.endsWith("/verification")) return jsonResponse(verifiedOutcome);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect((await screen.findByText("Verification status:")).closest("p")).toHaveTextContent("VERIFIED");
    expect(screen.queryByRole("button", { name: "Verify outcome" })).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/verify"))).toHaveLength(0);
  });

  it("does not expose resolution actions for a RUNNING verification", async () => {
    installFetch({
      aiConfigured: true,
      resolution: async () => jsonResponse([]),
      post: async (url) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(completedExecution);
        if (url.endsWith("/verification")) return jsonResponse({ ...verifiedOutcome, status: "running", completed_at: null });
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));

    expect((await screen.findByText("Verification status:")).closest("p")).toHaveTextContent("RUNNING");
    expect(screen.queryByRole("button", { name: "Resolve incident" })).not.toBeInTheDocument();
  });

  it.each([
    ["resolve", "resolved"],
    ["keep_open", "open"],
  ] as const)("records human %s with an optional reason", async (decision, expectedIncidentStatus) => {
    const fetchMock = installFetch({
      aiConfigured: true,
      resolution: async (_url, init) => init?.method === "POST"
        ? jsonResponse({
            ...resolvedDecision,
            decision,
            reason: "Operator reviewed stable health.",
          })
        : jsonResponse([]),
      post: async (url, init) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(completedExecution);
        if (url.endsWith("/verification")) return jsonResponse(verifiedOutcome);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));
    await screen.findByText("Verification status:");
    await userEvent.type(screen.getByLabelText("Reason"), "Operator reviewed stable health.");
    await userEvent.click(screen.getByRole("button", {
      name: decision === "resolve" ? "Resolve incident" : "Keep open",
    }));

    expect((await screen.findByText("Decision:")).closest("p")).toHaveTextContent(
      decision.replace("_", " ").toUpperCase(),
    );
    expect(screen.getByText("Reason:").closest("p")).toHaveTextContent("Operator reviewed stable health.");
    expect(screen.getByText("Incident status:").closest("p")).toHaveTextContent(expectedIncidentStatus.toUpperCase());
    expect(screen.queryByRole("button", { name: "Resolve incident" })).not.toBeInTheDocument();
    const request = fetchMock.mock.calls.find(([url, init]) =>
      String(url).endsWith("/resolution-decisions") && init?.method === "POST"
    );
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      verification_id: 60,
      decision,
      reason: "Operator reviewed stable health.",
    });
  });

  it("hydrates persisted RESOLVE history and does not expose Resolve again", async () => {
    installFetch({
      incidentsOverride: [{ ...incidents[0], status: "resolved" }, incidents[1]],
      resolution: async () => jsonResponse([resolvedDecision]),
      post: async (url) => {
        if (url.endsWith("/investigate")) {
          return jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [] });
        }
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("deterministic");

    expect((await screen.findByText("Decision:")).closest("p")).toHaveTextContent("RESOLVE");
    expect(screen.getByText("Incident status:").closest("p")).toHaveTextContent("RESOLVED");
    expect(screen.queryByRole("button", { name: "Resolve incident" })).not.toBeInTheDocument();
  });

  it("keeps the incident unchanged when resolution API rejects the decision", async () => {
    installFetch({
      aiConfigured: true,
      resolution: async (_url, init) => init?.method === "POST"
        ? jsonResponse({ detail: { code: "resolution_not_eligible", message: "Resolution rejected" } }, 409)
        : jsonResponse([]),
      post: async (url, init) => {
        if (url.endsWith("/investigate-ai")) return jsonResponse(actionableExecution);
        if (url.endsWith("/action-proposals")) return jsonResponse(executableProposal, 201);
        if (url.endsWith("/approve")) return jsonResponse({ ...executableProposal, approval_status: "approved" });
        if (url.endsWith("/execute")) return jsonResponse(completedExecution);
        if (url.endsWith("/verification")) return jsonResponse(verifiedOutcome);
        throw new Error(`Unexpected request: ${url}`);
      },
    });
    render(<App />);
    await selectIncident();
    await runInvestigation("ai");
    await userEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await userEvent.click(await screen.findByRole("button", { name: "Execute approved action" }));
    await userEvent.click(await screen.findByRole("button", { name: "Resolve incident" }));

    expect(await screen.findByText("Resolution rejected")).toBeVisible();
    expect(screen.getByText("Incident status:").closest("p")).toHaveTextContent("OPEN");
    expect(screen.getByRole("button", { name: "Resolve incident" })).toBeVisible();
  });
});
