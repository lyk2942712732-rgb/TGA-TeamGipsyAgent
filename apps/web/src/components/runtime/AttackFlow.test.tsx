import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { RuntimeSnapshot } from "../../runtime/event-types";
import { AttackFlow } from "./AttackFlow";

const snapshot: RuntimeSnapshot = {
  schema_version: 2,
  task: { id: "task", name: "Authorized challenge", mode: "ctf", target: "http://target.local", scope: ["target.local"] },
  session: { status: "running", turn_count: 1, max_turns: 8 },
  challenge: { status: "active", status_reason: "" },
  solvers: [{ id: "solver", role: "recon", status: "running" }],
  subagents: [],
  board: { hypotheses: [{ id: "hyp", statement: "Landing surface exposes a form", attack_class: "recon", entry_point: "/", rationale: "observed", next_test: "request", status: "testing", confidence: .8, attempt_count: 1, evidence_artifact_ids: [], last_result: "" }], memory: [] },
  actions: [{ id: "action", solver_id: "solver", capability: "http.request", target: "http://target.local", status: "succeeded", hypothesis_id: "hyp", artifact_ids: ["artifact"] }],
  artifacts: [{ id: "artifact", kind: "http_response", path: "response.json", tool: "http.request" }],
  flags: [], findings: [], events: [], latest_seq: 0,
};

describe("AttackFlow", () => {
  it("projects challenge, strategy and action without demo nodes", () => {
    render(<AttackFlow snapshot={snapshot} />);
    expect(screen.getByTestId("flow-challenge")).toHaveTextContent("Authorized challenge");
    expect(screen.getByTestId("flow-hypothesis")).toHaveTextContent("Landing surface exposes a form");
    expect(screen.getByTestId("flow-action")).toHaveTextContent("http.request");
    expect(screen.getByRole("button", { name: "Evidence 1" })).toBeInTheDocument();
  });
});
