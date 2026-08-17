import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { TeamExplorer } from "../team/TeamExplorer";
import { GlobalActionDock } from "./components/GlobalActionDock";
import { ReplayControls } from "./components/ReplayControls";
import { SolverInspector } from "./components/SolverInspector";
import { TaskCommandHeader } from "./components/TaskCommandHeader";
import { TaskWorkspaceTabs } from "./components/TaskWorkspaceTabs";
import { selectSupervisor } from "./models/selectors";
import { replayStoreAtSeq } from "./models/replay";
import type { RuntimeStore } from "./models/types";
import { runtimeApi } from "../../runtime/api-v2";
import { readRuntimeSelection, writeRuntimeSelection, type RuntimeTab } from "./runtime-selection";
import { useTaskRuntime } from "./use-task-runtime";

export function TaskRuntimePage({ taskId, mode = "runtime" }: { taskId: string; mode?: "runtime" | "replay" }) {
  const { store, connection, error, refresh } = useTaskRuntime(taskId, { live: mode === "runtime" });
  const location = useLocation();
  const navigate = useNavigate();
  const [drawer, setDrawer] = useState<"team" | "inspector" | null>(null);
  const [chatOpenNonce, setChatOpenNonce] = useState(0);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const selection = useMemo(() => readRuntimeSelection(location.search), [location.search]);
  const setSelection = (patch: Parameters<typeof writeRuntimeSelection>[1]) => navigate({ pathname: location.pathname, search: writeRuntimeSelection(location.search, patch) }, { replace: true });

  useEffect(() => { if (mode === "replay" && store) setReplaySeq((current) => current ?? store.latestSeq); }, [mode, store]);

  if (!store) return <section className="task-runtime-loading" aria-live="polite"><h1>正在加载任务运行时</h1><p>{error ?? "正在读取 Snapshot 并连接事件流。"}</p>{error ? <button onClick={refresh}>重试</button> : null}</section>;
  const viewStore = mode === "replay" && replaySeq !== null ? replayStoreAtSeq(store, replaySeq) : store;
  const currentIntent = selection.intentId
    ? viewStore.intentsById[selection.intentId]
    : Object.values(viewStore.intentsById).find((intent) => ["running", "reviewing", "awaiting_approval", "failed", "blocked"].includes(intent.status));
  const intentSolver = currentIntent?.assignedSolverId ?? null;
  const supervisor = selectSupervisor(viewStore);
  const activeSolver = Object.values(viewStore.solversById).find((solver) => ["running", "awaiting_approval", "awaiting_user_input", "failed"].includes(solver.status));
  const selectedSolver = (selection.solverId ? viewStore.solversById[selection.solverId] : undefined)
    ?? (intentSolver ? viewStore.solversById[intentSolver] : undefined)
    ?? activeSolver
    ?? supervisor;
  const selectedSolverId = selectedSolver?.solverId ?? null;
  const openSolverChat = () => { setChatOpenNonce((value) => value + 1); setDrawer("inspector"); };
  const terminalFailure = taskFailure(viewStore);
  const control = async (action: "cancel") => { setBusy(true); setNotice(null); try { const result = await runtimeApi.control(taskId, action); setNotice(result.accepted === false ? (result.reason ?? "当前 Runtime 不支持该控制操作") : "Task 控制请求已提交"); refresh(); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "Task 控制失败"); } finally { setBusy(false); } };
  return <section className="task-runtime-page">
    <TaskCommandHeader store={viewStore} connection={connection} mode={mode} busy={busy} onControl={(action) => void control(action)} onIntervention={openSolverChat} onApprovals={() => setSelection({ tab: "approvals" })} onReplay={() => navigate({ pathname: `/tasks/${encodeURIComponent(taskId)}/replay`, search: location.search })} />
    {mode === "replay" && replaySeq !== null ? <ReplayControls store={store} seq={replaySeq} onSeq={setReplaySeq} /> : null}
    {error ? <div className="runtime-sync-error" role="alert">实时同步暂时中断：{error}<button onClick={refresh}>重试</button></div> : null}
    {terminalFailure ? <div className="runtime-sync-error runtime-task-failure" role="alert">
      <strong>{terminalFailure.title}</strong>
      <span>{terminalFailure.message}</span>
      {terminalFailure.attempts ? <small>已自动尝试 {terminalFailure.attempts} 次</small> : null}
    </div> : null}
    {notice ? <div className="runtime-sync-notice" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    <div className="runtime-mobile-switches"><button aria-expanded={drawer === "team"} onClick={() => setDrawer(drawer === "team" ? null : "team")}>团队</button><button aria-expanded={drawer === "inspector"} onClick={() => setDrawer(drawer === "inspector" ? null : "inspector")}>检查器</button></div>
    <div className="task-runtime-layout">
      <div className="runtime-side runtime-team-side" data-open={drawer === "team"}><TeamExplorer store={viewStore} selectedSolverId={selectedSolverId} onSelect={(solverId) => { setSelection({ solverId }); setDrawer(null); }} onDetails={() => { setSelection({ tab: "overview" }); setDrawer(null); }} /></div>
      <main><TaskWorkspaceTabs store={viewStore} tab={selection.tab} selectedSolverId={selectedSolverId} selectedIntentId={selection.intentId} readonly={mode === "replay"} onChanged={refresh} onTab={(tab: RuntimeTab) => setSelection({ tab })} onSolver={(solverId) => setSelection({ solverId })} onIntent={(intentId) => setSelection({ intentId, solverId: viewStore.intentsById[intentId]?.assignedSolverId ?? selection.solverId })} /></main>
      <div className="runtime-side runtime-inspector-side" data-open={drawer === "inspector"}><SolverInspector store={viewStore} solver={selectedSolver ?? null} readonly={mode === "replay"} chatOpenNonce={chatOpenNonce} onChanged={refresh} /></div>
    </div>
    <GlobalActionDock store={viewStore} mode={mode} onRefresh={refresh} onOpenApprovals={() => setSelection({ tab: "approvals" })} onIntervention={openSolverChat} />
  </section>;
}

function taskFailure(store: RuntimeStore): { title: string; message: string; retryable: boolean; attempts: number | null } | null {
  if (!["blocked", "failed"].includes(store.session.status)) return null;
  const events = Object.values(store.eventsBySeq).sort((left, right) => right.seq - left.seq);
  const failed = events.find((event) => event.type === "TASK_FAILED");
  const retryable = failed?.payload.retryable === true;
  const rawAttempts = failed?.payload.attempts;
  const attempts = typeof rawAttempts === "number" && Number.isFinite(rawAttempts) && rawAttempts > 0 ? rawAttempts : null;
  const message = stringValue(failed?.payload.message)
    ?? store.session.stopReason
    ?? "任务运行时发生未分类错误。";
  const title = ({
    AuthenticationError: "模型认证失败",
    APITimeoutError: "模型请求超时",
    ModelCallLimitExceededError: "模型未能生成有效结构化结果",
    BudgetExceededError: "任务预算已耗尽",
    TaskCancelledError: "任务已取消",
  } as Record<string, string>)[stringValue(failed?.payload.error_type) ?? ""] ?? "任务运行失败";
  return { title, message, retryable, attempts };
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}
