import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { AOEOverview, AOEQuestion, AOEScoutWatch } from "../../../runtime/tga3-runtime";
import { workerRecords } from "../aoe-view";
import { AOEFinishConfirmDialog } from "./AOEFinishConfirmDialog";
import { AOEScoutStandbyBar } from "./AOEScoutStandbyBar";

function makeWatch(overrides: Partial<AOEScoutWatch> = {}): AOEScoutWatch {
  return {
    mode: "sentinel",
    enabled: true,
    state: "watching",
    probe_script_present: true,
    probe_interval_seconds: 45,
    llm_interval_seconds: null,
    next_check_at: Date.now() / 1000 + 30,
    last_checked_at: Date.now() / 1000 - 15,
    last_check_kind: "sentinel",
    last_result: "零 Token 哨兵探测正常 · 暂无新题",
    last_new_count: 0,
    last_solved_sync_count: 0,
    last_error: null,
    consecutive_failures: 0,
    scan_round: 1,
    sentinel_checks_total: 4,
    ...overrides,
  };
}

function makeOverview(watchOverrides: Partial<AOEScoutWatch> = {}, extra: Partial<AOEOverview> = {}): AOEOverview {
  return {
    task_id: "task-1",
    phase: "solo",
    attempt_seconds: 1200,
    collection_complete: true,
    scout_collecting: false,
    scout_watch: makeWatch(watchOverrides),
    questions: [],
    ...extra,
  };
}

describe("AOEScoutStandbyBar", () => {
  it("renders zero-token sentinel status and supports manual wake and stop", async () => {
    const wakeSpy = vi.spyOn(tga3RuntimeApi, "wakeScout").mockResolvedValue({
      started: true,
      reason: "manual",
      scout_watch: makeWatch({ state: "collecting" }),
    });
    const stopSpy = vi.spyOn(tga3RuntimeApi, "stopScout").mockResolvedValue({
      stopped: true,
      mode: "watch_disabled",
      scout_watch: makeWatch({ enabled: false, mode: "manual", state: "idle" }),
    });
    const onChanged = vi.fn();

    render(<AOEScoutStandbyBar taskId="task-1" taskState="running" overview={makeOverview()} onChanged={onChanged} />);

    expect(screen.getByText(/零 Token 哨兵站岗中/)).toBeInTheDocument();
    expect(screen.getByText(/0 Token · 已探测 4 次/)).toBeInTheDocument();
    // When zero-token sentinel is active, hide periodic LLM select to avoid accidentally switching to token-burning polling.
    expect(screen.queryByLabelText("Scout 定时 LLM 巡检间隔")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /唤醒 Scout 巡检/ }));
    await waitFor(() => expect(wakeSpy).toHaveBeenCalledWith("task-1"));
    expect(onChanged).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /停止站岗/ }));
    await waitFor(() => expect(stopSpy).toHaveBeenCalledWith("task-1"));

    wakeSpy.mockRestore();
    stopSpy.mockRestore();
  });

  it("shows manual standby message and explicit token-cost LLM poll option when no probe script exists", () => {
    render(
      <AOEScoutStandbyBar
        taskId="task-1"
        taskState="running"
        overview={makeOverview({
          mode: "manual",
          enabled: false,
          state: "idle",
          probe_script_present: false,
          next_check_at: null,
        })}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.getByText(/未检测到零 Token 探测脚本/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /唤醒 Scout 巡检/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /零 Token 哨兵/ })).not.toBeInTheDocument();
    const select = screen.getByLabelText("Scout 定时 LLM 巡检间隔");
    expect(select).toBeInTheDocument();
    expect(screen.getAllByText(/耗 Token/)).toHaveLength(4);
  });

  it("uses clear recover/retry labels when sentinel was paused or circuit-broken", () => {
    const { rerender } = render(
      <AOEScoutStandbyBar
        taskId="task-1"
        taskState="running"
        overview={makeOverview({
          mode: "manual",
          enabled: false,
          state: "idle",
          probe_script_present: true,
          next_check_at: null,
        })}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /恢复零 Token 哨兵/ })).toBeInTheDocument();

    rerender(
      <AOEScoutStandbyBar
        taskId="task-1"
        taskState="running"
        overview={makeOverview({
          mode: "sentinel",
          enabled: false,
          state: "error_paused",
          consecutive_failures: 3,
          probe_script_present: true,
          next_check_at: null,
        })}
        onChanged={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /重试零 Token 哨兵/ })).toBeInTheDocument();
  });
});

describe("AOEFinishConfirmDialog", () => {
  it("explains zero-token sentinel standby and submits continue decision with checkbox", () => {
    const onContinue = vi.fn();
    const onFinish = vi.fn();
    render(
      <AOEFinishConfirmDialog
        open
        taskTitle="AWD 持续放题赛"
        confirm={{ status: "pending", wave_question_count: 5, solved_count: 4, abandoned_count: 1, pending_reports: 1 }}
        watch={makeWatch()}
        busy={false}
        error={null}
        onContinueStandby={onContinue}
        onFinish={onFinish}
        onMinimize={vi.fn()}
      />,
    );

    expect(screen.getByText(/继续任务将保持默认零 Token 哨兵站岗/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/后续波次全部完题后自动保持站岗/));
    fireEvent.click(screen.getByRole("button", { name: /继续任务，Scout 站岗候题/ }));
    expect(onContinue).toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: /跳过剩余报告，立即强制结束/ }));
    expect(onFinish).toHaveBeenCalledWith(true);
  });

  it("explains manual standby clearly when no probe script exists", () => {
    render(
      <AOEFinishConfirmDialog
        open
        taskTitle="手动巡检赛"
        confirm={{ status: "pending", wave_question_count: 2, solved_count: 2, abandoned_count: 0, pending_reports: 0 }}
        watch={makeWatch({ probe_script_present: false, mode: "manual", enabled: false, state: "idle" })}
        busy={false}
        error={null}
        onContinueStandby={vi.fn()}
        onFinish={vi.fn()}
        onMinimize={vi.fn()}
      />,
    );

    expect(screen.getByText(/当前未检测到零 Token 探测脚本/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /立即结束任务/ })).toBeInTheDocument();
  });
});

describe("platform_solved attribution", () => {
  it("does not credit an interrupted worker with a win when platform_solved settles the question", () => {
    const question: AOEQuestion = {
      question_id: "q-ext",
      parent_task_id: "task-1",
      title: "External Solved Challenge",
      description: "desc",
      source_key: "q-ext",
      difficulty: "easy",
      connection_info: "",
      submission_instructions: "",
      status: "solved",
      finish_reason: "platform_solved",
      report_state: "platform_solved",
      attempted_by: ["worker-openai"],
      current_agents: [],
      attempts: [
        {
          id: "att-1",
          agent_id: "worker-openai",
          phase: "solo",
          started_at: 1000,
          finished_at: 1090,
          deadline: 2200,
          outcome: "interrupted",
          success_credit: false,
        },
      ],
      flag: null,
      blackboard_seq: 1,
      attachments: [],
    };
    const records = workerRecords([question], "worker-openai", 1100);
    expect(records).toHaveLength(1);
    expect(records[0].successful).toBe(false);
    expect(records[0].assisted).toBe(false);
  });
});
