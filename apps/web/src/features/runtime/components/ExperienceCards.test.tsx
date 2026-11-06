import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ExperienceCards, type ExperienceCard } from "./ExperienceCards";

afterEach(() => vi.unstubAllGlobals());
const card: ExperienceCard = {
  card_id: "exp-123", title: "从图片尾部提取归档",
  applies: { signals: ["png"], requires: ["先确认归档结构"] },
  steps: ["解析并提取附加数据"], verify: ["归档能够正常解包"], limits: ["未测试加密归档"],
  resources: ["assets/exp-123/extract.py"],
  sources: [{ task_id: "parent", question_id: "q1", action_ids: ["worker-action-1"], validation: "reproduced" }],
};

it("loads only this task and exposes method, JSON, sources and scoped resource download", async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ cards: [card], state: {} }) });
  vi.stubGlobal("fetch", fetch);
  render(<ExperienceCards taskId="parent" />);
  expect(await screen.findByText(card.title)).toBeInTheDocument();
  fireEvent.click(screen.getByText(card.title));
  expect(screen.getByText("先确认归档结构")).toBeVisible();
  expect(screen.getByText("解析并提取附加数据")).toBeVisible();
  expect(screen.getByRole("link", { name: "extract.py" }).getAttribute("href")).toContain("/tasks/parent/experience/exp-123/resources/assets/exp-123/extract.py");
  expect(screen.getByRole("link", { name: "查看来源题目" })).toHaveAttribute("href", "/tasks/parent/runtime?tab=blackboard&question=q1");
  fireEvent.click(screen.getByText("查看完整 JSON"));
  expect(document.querySelector("pre")).toHaveTextContent('"card_id": "exp-123"');
  expect(fetch.mock.calls[0][0]).toContain("/tasks/parent/experience");
});

it("explains empty cards and shows generation errors without inventing data", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({
    cards: [], state: { q1: { status: "error", error: "missing action evidence" } },
  }) }));
  render(<ExperienceCards taskId="parent" />);
  expect(await screen.findByText(/尚无经验卡/)).toBeInTheDocument();
  expect(screen.getByText(/missing action evidence/)).toBeInTheDocument();
});
