import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn(), resume: vi.fn() }));
vi.mock("../api/tga3-attention", () => ({ attentionApi: mocks }));
import { ApprovalsPage } from "./ApprovalsPage";

const paused = { id: "paused:worker", kind: "agent_paused", task_id: "task-1", task_title: "题目一", task_state: "running", agent_id: "worker-openai", title: "Worker 已暂停", detail: "手动暂停", created_at: "2026-08-21T00:00:00Z" };

describe("ApprovalsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.list.mockResolvedValue([paused]); mocks.resume.mockResolvedValue({}); });
  it("shows runtime attention without a chat composer", async () => {
    render(<MemoryRouter><ApprovalsPage /></MemoryRouter>);
    expect(await screen.findByText("手动暂停")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "提交回答" })).not.toBeInTheDocument();
  });
  it("shows an empty real queue", async () => {
    mocks.list.mockResolvedValue([]);
    render(<MemoryRouter><ApprovalsPage /></MemoryRouter>);
    expect(await screen.findByText("当前没有待处理事项")).toBeInTheDocument();
  });
});
