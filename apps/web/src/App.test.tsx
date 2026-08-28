import { render, screen, waitFor } from "@testing-library/react";
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(options?: {
  coreFailure?: boolean;
  aiConfigured?: boolean;
  post?: (url: string) => Promise<Response>;
}) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/health")) {
      return options?.coreFailure
        ? jsonResponse({ detail: "unavailable" }, 503)
        : jsonResponse({ status: "ok", service: "agentic-supportops" });
    }
    if (url.endsWith("/incidents")) return jsonResponse(incidents);
    if (url.endsWith("/ai/config")) {
      return jsonResponse({ configured: options?.aiConfigured ?? false });
    }
    if (options?.post) return options.post(url);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function selectIncident(title = "Disk usage alert") {
  await userEvent.click(await screen.findByRole("button", { name: new RegExp(title) }));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Agentic SupportOps operator workflow", () => {
  it("shows loading before rendering incidents and backend health", async () => {
    installFetch();
    render(<App />);

    expect(screen.getByText("Checking backend health…")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /INC-001.*Disk usage alert/ })).toBeVisible();
    expect(screen.getByText("Backend online — agentic-supportops")).toBeVisible();
  });

  it("shows the existing unavailable state when core loading fails", async () => {
    installFetch({ coreFailure: true });
    render(<App />);

    expect(await screen.findByText("Backend unavailable")).toBeVisible();
    expect(screen.queryByRole("button", { name: /INC-001/ })).not.toBeInTheDocument();
  });

  it("selects incidents and clears investigation output from the previous selection", async () => {
    installFetch({
      post: async () =>
        jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [deterministicEvidence] }),
    });
    render(<App />);

    await selectIncident();
    await userEvent.click(screen.getByRole("button", { name: "Run deterministic" }));
    expect(await screen.findByText(/"used_percent": 94/)).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: /DNS resolution failure/ }));
    expect(screen.getByRole("heading", { name: "DNS resolution failure" })).toBeVisible();
    expect(screen.queryByText(/"used_percent": 94/)).not.toBeInTheDocument();
    expect(screen.queryByText("Mode: deterministic")).not.toBeInTheDocument();
  });

  it("disables controls while a deterministic investigation runs and renders its evidence", async () => {
    let resolveInvestigation!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      resolveInvestigation = resolve;
    });
    installFetch({ post: async () => pending });
    render(<App />);

    await selectIncident();
    await userEvent.click(screen.getByRole("button", { name: "Run deterministic" }));
    expect(screen.getByRole("button", { name: "Running deterministic…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "AI unavailable" })).toBeDisabled();
    expect(screen.getByRole("status")).toHaveTextContent("Investigation in progress");

    resolveInvestigation(
      jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [deterministicEvidence] }),
    );
    expect(await screen.findByText(/"used_percent": 94/)).toBeVisible();
    expect(screen.getByText("Mode: deterministic")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run deterministic" })).toBeEnabled();
  });

  it("surfaces a structured investigation conflict without leaking protocol details", async () => {
    installFetch({
      post: async () => jsonResponse({ detail: { code: "run_conflict", message: "Investigation already running" } }, 409),
    });
    render(<App />);

    await selectIncident();
    await userEvent.click(screen.getByRole("button", { name: "Run deterministic" }));

    expect(await screen.findByText("Investigation already running")).toBeVisible();
    expect(screen.getByText("Mode: deterministic")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run deterministic" })).toBeEnabled();
  });

  it("distinguishes a configured AI investigation and renders its validated result", async () => {
    installFetch({
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
    const aiButton = await screen.findByRole("button", { name: "Run AI investigation" });
    await userEvent.click(aiButton);

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
  });

  it("does not let an older investigation response overwrite a newly selected incident", async () => {
    let resolveOldRequest!: (response: Response) => void;
    const oldRequest = new Promise<Response>((resolve) => {
      resolveOldRequest = resolve;
    });
    installFetch({ post: async () => oldRequest });
    render(<App />);

    await selectIncident();
    await userEvent.click(screen.getByRole("button", { name: "Run deterministic" }));
    await userEvent.click(screen.getByRole("button", { name: /DNS resolution failure/ }));
    resolveOldRequest(
      jsonResponse({ incident_id: 1, catalog_id: "INC-001", steps: [], evidence: [deterministicEvidence] }),
    );

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "DNS resolution failure" })).toBeVisible();
      expect(screen.queryByText(/"used_percent": 94/)).not.toBeInTheDocument();
      expect(screen.queryByText("Mode: deterministic")).not.toBeInTheDocument();
    });
  });
});
