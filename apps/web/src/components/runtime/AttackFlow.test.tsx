import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RuntimeSnapshot } from "../../runtime/event-types";
import { AttackFlow, buildTopologyGraph } from "./AttackFlow";

const snapshot: RuntimeSnapshot = {
  schema_version: 2,
  task: { id: "task", name: "Authorized challenge", mode: "ctf", target: "http://target.local", scope: ["target.local"] },
  session: { status: "running", turn_count: 1, max_turns: 8 },
  challenge: { status: "active", status_reason: "" },
  solvers: [{ id: "solver", role: "recon", status: "running" }],
  subagents: [],
  board: { hypotheses: [{ id: "hyp", statement: "Landing surface exposes a form", attack_class: "recon", entry_point: "/", rationale: "observed", next_test: "request", status: "testing", confidence: .8, attempt_count: 1, evidence_artifact_ids: [], last_result: "" }], memory: [] },
  actions: [
    { id: "action", solver_id: "solver", capability: "http.request", target: "http://target.local", status: "succeeded", hypothesis_id: "hyp", artifact_ids: ["artifact"], arguments: { method: "GET" }, summary: "HTTP 200 from http://target.local (320 ms)" },
    { id: "action-2", solver_id: "solver", capability: "http.request", target: "http://target.local/login", status: "succeeded", hypothesis_id: "hyp", artifact_ids: ["flag-artifact"], arguments: { method: "POST", body: "code=%3Bcat%20flag.php" }, summary: "HTTP 200 from http://target.local/login (640 ms)" },
  ],
  artifacts: [{ id: "artifact", kind: "http_response", path: "response.json", tool: "http.request" }, { id: "flag-artifact", kind: "http_response", path: "flag.json", tool: "http.request" }],
  flags: [{ value: "CTF{confirmed-proof}", evidence_artifact_id: "flag-artifact" }], findings: [], events: [], latest_seq: 0,
};

describe("AttackFlow", () => {
  it("lays Session tools out as a left-to-right pulse chain", () => {
    const graph = buildTopologyGraph(snapshot, []);
    const manager = graph.nodes.find((item) => item.id === "manager")!;
    const solver = graph.nodes.find((item) => item.id === "solver:solver")!;
    const actions = graph.nodes.filter((item) => item.id.startsWith("action:"));

    expect(manager.position.x).toBeLessThan(solver.position.x);
    expect(solver.position.x).toBeLessThan(actions[0]!.position.x);
    expect(actions[0]!.position.x).toBeLessThan(actions[1]!.position.x);
    expect(actions[0]!.position.y).not.toBe(actions[1]!.position.y);
    expect(graph.edges.filter((item) => item.className?.includes("process")).every((item) => item.type === "step")).toBe(true);
  });

  it("projects challenge, strategy and action without demo nodes", () => {
    render(<AttackFlow snapshot={snapshot} />);
    expect(screen.getByTestId("flow-challenge")).toHaveTextContent("Authorized challenge");
    expect(screen.getByTestId("flow-hypothesis")).toHaveTextContent("Landing surface exposes a form");
    expect(screen.getAllByTestId("flow-action")).toHaveLength(2);
    expect(screen.getAllByTestId("flow-action")[0]).toHaveTextContent("1. GET /");
    expect(screen.getAllByTestId("flow-action")[1]).toHaveTextContent("2. POST code = ;cat flag.php");
    expect(screen.getByTestId("flow-action-flag")).toHaveTextContent("FLAG FOUND");
    expect(screen.getByText("1 solver · 2 native · 0 MCP")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evidence 2" })).toBeInTheDocument();
  });

  it("renders artifact request and response as readable evidence inside the drawer", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        artifact: { id: "flag-artifact", kind: "http_response" },
        preview: JSON.stringify({
          method: "POST",
          requested_url: "http://target.local/login",
          body: "code=%3Bcat%20flag.php",
          status: 200,
          duration_ms: 640,
          content_type: "text/html",
          body_excerpt: "<html><style>body{color:red}</style><div class='result'>flag.php<br>CTF{confirmed-proof}</div></html>",
        }),
      }),
    } as Response);

    render(<AttackFlow snapshot={snapshot} />);
    fireEvent.click(screen.getByTestId("flow-action-flag"));

    expect(await screen.findByTestId("formatted-artifact")).toHaveTextContent("Readable request & response");
    expect(screen.getByTestId("formatted-artifact")).toHaveTextContent("code = ;cat flag.php");
    expect(screen.getByTestId("formatted-artifact")).toHaveTextContent("CTF{confirmed-proof}");
    expect(screen.getByTestId("flag-proof")).toHaveTextContent("This tool call produced the confirmed evidence.");
    fetchMock.mockRestore();
  });

  it("follows new events while Live is active and stops following after manual scrubbing", async () => {
    const event = (seq: number) => ({ id: `event-${seq}`, task_id: "task", seq, type: "MESSAGE_END", payload: { content: `turn ${seq}` }, created_at: `2026-07-17T00:00:0${seq}Z` });
    const first = { ...snapshot, events: [event(1)], latest_seq: 1 };
    const { rerender } = render(<AttackFlow snapshot={first} />);
    const slider = screen.getByRole("slider", { name: "事件回放位置" });
    expect(slider).toHaveValue("1");

    rerender(<AttackFlow snapshot={{ ...first, events: [event(1), event(2)], latest_seq: 2 }} />);
    await waitFor(() => expect(slider).toHaveValue("2"));

    fireEvent.change(slider, { target: { value: "1" } });
    rerender(<AttackFlow snapshot={{ ...first, events: [event(1), event(2), event(3)], latest_seq: 3 }} />);
    await waitFor(() => expect(slider).toHaveValue("1"));
  });

  it("replays context, tool calls, artifacts and flags on the same cursor as the timeline", async () => {
    const replayEvents = [
      { id: "event-1", task_id: "task", seq: 1, type: "USER_HINT", payload: { memory_id: "memory", content: "try the form" }, created_at: "2026-07-17T00:00:01Z" },
      { id: "event-2", task_id: "task", solver_id: "solver", seq: 2, type: "SESSION_STARTED", payload: {}, created_at: "2026-07-17T00:00:02Z" },
      { id: "event-3", task_id: "task", solver_id: "solver", seq: 3, type: "TOOL_EXECUTION_START", payload: { action_id: "action" }, created_at: "2026-07-17T00:00:03Z" },
      { id: "event-4", task_id: "task", solver_id: "solver", seq: 4, type: "TOOL_EXECUTION_END", payload: { action_id: "action", status: "succeeded", artifacts: [{ artifact_id: "artifact" }] }, created_at: "2026-07-17T00:00:04Z" },
      { id: "event-5", task_id: "task", solver_id: "solver", seq: 5, type: "TOOL_EXECUTION_START", payload: { action_id: "action-2" }, created_at: "2026-07-17T00:00:05Z" },
      { id: "event-6", task_id: "task", solver_id: "solver", seq: 6, type: "FLAG_FOUND", payload: { value: "CTF{confirmed-proof}", artifact_id: "flag-artifact" }, created_at: "2026-07-17T00:00:06Z" },
    ];
    const replaySnapshot = {
      ...snapshot,
      board: { ...snapshot.board, memory: [{ id: "memory", kind: "hint" as const, content: "try the form", artifact_ids: [], source: "user" }] },
      events: replayEvents,
      latest_seq: 6,
    };
    render(<AttackFlow snapshot={replaySnapshot} mode="replay" />);
    const slider = screen.getByRole("slider", { name: "事件回放位置" });

    fireEvent.change(slider, { target: { value: "0" } });
    await waitFor(() => expect(screen.queryAllByTestId("flow-action")).toHaveLength(0));
    expect(screen.queryByTestId("flow-memory")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evidence 0" })).toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "1" } });
    await waitFor(() => expect(screen.getByTestId("flow-memory")).toHaveTextContent("try the form"));

    fireEvent.change(slider, { target: { value: "3" } });
    await waitFor(() => expect(screen.queryAllByTestId("flow-action")).toHaveLength(1));
    expect(screen.getByTestId("flow-action")).toHaveTextContent("running · 0 artifacts");

    fireEvent.change(slider, { target: { value: "4" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Evidence 1" })).toBeInTheDocument());

    fireEvent.change(slider, { target: { value: "5" } });
    await waitFor(() => expect(screen.queryAllByTestId("flow-action")).toHaveLength(2));
    expect(screen.queryByTestId("flow-action-flag")).not.toBeInTheDocument();

    fireEvent.change(slider, { target: { value: "6" } });
    await waitFor(() => expect(screen.getByTestId("flow-action-flag")).toHaveTextContent("FLAG FOUND"));
    expect(screen.getByRole("button", { name: "Evidence 2" })).toBeInTheDocument();
  });

  it("shows native MCP server, method, duration and Artifact provenance", () => {
    const mcpSnapshot: RuntimeSnapshot = {
      ...snapshot,
      actions: [{ id: "mcp-action", solver_id: "solver", capability: "mcp__fixture__echo", target: "http://target.local", status: "succeeded", artifact_ids: ["mcp-artifact"], arguments: { text: "safe" }, summary: "MCP fixture.echo returned 1 content block" }],
      artifacts: [{ id: "mcp-artifact", kind: "tool_output", path: "result.mcp.json", tool: "mcp__fixture__echo" }],
      flags: [],
      events: [{ id: "mcp-end", task_id: "task", solver_id: "solver", seq: 1, type: "TOOL_EXECUTION_END", payload: { action_id: "mcp-action", tool_kind: "mcp", mcp_server: "fixture", mcp_method: "echo", status: "succeeded", duration_ms: 42, artifact_ids: ["mcp-artifact"], trace_id: "trace_test" }, created_at: "2026-07-21T00:00:00Z" }],
      latest_seq: 1,
    };
    render(<AttackFlow snapshot={mcpSnapshot} />);
    expect(screen.getByTestId("flow-action")).toHaveTextContent("fixture:echo");
    expect(screen.getByText(/1 solver.*0 native.*1 MCP/)).toBeInTheDocument();
    expect(screen.getByText(/fixture\.echo.*42 ms/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evidence 1" })).toBeInTheDocument();
  });
});
