import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ list: vi.fn(), answer: vi.fn(), resume: vi.fn() }));
vi.mock("../api/tga3-attention", () => ({ attentionApi: mocks }));
import { ApprovalsPage } from "./ApprovalsPage";

const question = { id: "question:q1", kind: "question", task_id: "task-1", task_title: "题目一", task_state: "waiting_user", agent_id: "worker-openai", title: "等待用户回答", detail: "目标 URL 是什么？", question_id: "q1", created_at: "2026-08-21T00:00:00Z" };

describe("ApprovalsPage", () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.list.mockResolvedValue([question]); mocks.answer.mockResolvedValue({}); mocks.resume.mockResolvedValue({}); });
  it("shows TGA3 questions and submits the answer", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><ApprovalsPage /></MemoryRouter>);
    expect(await screen.findByText("目标 URL 是什么？")).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText(/输入回答/), "http://target.test");
    await user.click(screen.getByRole("button", { name: "提交回答" }));
    await waitFor(() => expect(mocks.answer).toHaveBeenCalledWith("q1", "http://target.test"));
  });
  it("shows an empty real queue", async () => {
    mocks.list.mockResolvedValue([]);
    render(<MemoryRouter><ApprovalsPage /></MemoryRouter>);
    expect(await screen.findByText("当前没有待处理事项")).toBeInTheDocument();
  });
});
