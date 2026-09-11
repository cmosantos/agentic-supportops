import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it } from "vitest";

import { InvestigationReview } from "./InvestigationReview";


it("renders the persisted investigation contract for a deterministic run", async () => {
  const objective = "Determine whether the incident is caused by DNS evidence.";
  render(
    <InvestigationReview
      mode="deterministic"
      run={{
        id: 21,
        incident_id: 1,
        mode: "deterministic",
        status: "completed",
        model: "deterministic-playbook",
        response_id: null,
        goal_snapshot: {
          objective,
          success_criteria: ["Use persisted tool evidence."],
          constraints: ["Use read-only investigation tools."],
          human_action_required: true,
        },
        result: null,
        usage: {
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          response_iterations: 0,
        },
        error: null,
        created_at: "2026-09-11T10:00:00Z",
        completed_at: "2026-09-11T10:00:01Z",
      }}
      status="completed"
      result={null}
      evidence={[]}
      steps={[]}
      events={[]}
      proposal={null}
      loading={false}
      error={null}
    />,
  );

  expect(
    screen.getByText("No model assessment is persisted for this investigation."),
  ).toBeVisible();
  const summary = screen.getByText("Investigation Contract");
  await userEvent.click(summary);
  expect(screen.getByText(objective)).toBeVisible();
  expect(
    screen.getByText(
      /application-owned contract governed historical investigation run #21/i,
    ),
  ).toBeVisible();
});
