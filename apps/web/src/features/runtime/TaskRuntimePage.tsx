import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { TeamExplorer } from "../team/TeamExplorer";
import { GlobalActionDock } from "./components/GlobalActionDock";
import { InterventionDialog } from "./components/InterventionDialog";
import { ReplayControls } from "./components/ReplayControls";
import { SolverInspector } from "./components/SolverInspector";
import { TaskCommandHeader } from "./components/TaskCommandHeader";
import { TaskWorkspaceTabs } from "./components/TaskWorkspaceTabs";
import { selectSupervisor } from "./models/selectors";
import { replayStoreAtSeq } from "./models/replay";
import { runtimeApi } from "../../runtime/api-v2";
import { readRuntimeSelection, writeRuntimeSelection, type RuntimeTab } from "./runtime-selection";
import { useTaskRuntime } from "./use-task-runtime";

export function TaskRuntimePage({ taskId, mode = "runtime" }: { taskId: string; mode?: "runtime" | "replay" }) {
  const { store, connection, error, refresh } = useTaskRuntime(taskId, { live: mode === "runtime" });
  const location = useLocation();
  const navigate = useNavigate();
  const [drawer, setDrawer] = useState<"team" | "inspector" | null>(null);
  const [interventionOpen, setInterventionOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [replaySeq, setReplaySeq] = useState<number | null>(null);
  const selection = useMemo(() => readRuntimeSelection(location.search), [location.search]);
  const setSelection = (patch: Parameters<typeof writeRuntimeSelection>[1]) => navigate({ pathname: location.pathname, search: writeRuntimeSelection(location.search, patch) }, { replace: true });

  useEffect(() => { if (mode === "replay" && store) setReplaySeq((current) => current ?? store.latestSeq); }, [mode, store]);

  if (!store) return <section className="task-runtime-loading" aria-live="polite"><h1>正在加载任务运行时</h1><p>{error ?? "正在读取 Snapshot 并连接事件流。"}</p>{error ? <button onClick={refresh}>重试</button> : null}</section>;
  const viewStore = mode === "replay" && replaySeq !== null ? replayStoreAtSeq(store, replaySeq) : store;
  const intentSolver = selection.intentId ? viewStore.intentsById[selection.intentId]?.assignedSolverId : null;
  const supervisor = selectSupervisor(viewStore);
  const selectedSolver = (selection.solverId ? viewStore.solversById[selection.solverId] : undefined) ?? (intentSolver ? viewStore.solversById[intentSolver] : undefined) ?? supervisor;
  const selectedSolverId = selectedSolver?.solverId ?? null;
  const control = async (action: "pause" | "resume" | "cancel") => { setBusy(true); setNotice(null); try { await runtimeApi.control(taskId, action); setNotice("Task 控制请求已提交"); refresh(); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "Task 控制失败"); } finally { setBusy(false); } };
  return <section className="task-runtime-page">
    <TaskCommandHeader store={viewStore} connection={connection} mode={mode} busy={busy} onControl={(action) => void control(action)} onIntervention={() => setInterventionOpen(true)} onApprovals={() => setSelection({ tab: "approvals" })} onReplay={() => navigate({ pathname: `/tasks/${encodeURIComponent(taskId)}/replay`, search: location.search })} />
    {mode === "replay" && replaySeq !== null ? <ReplayControls store={store} seq={replaySeq} onSeq={setReplaySeq} /> : null}
    {error ? <div className="runtime-sync-error" role="alert">实时同步暂时中断：{error}<button onClick={refresh}>重试</button></div> : null}
    {notice ? <div className="runtime-sync-notice" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    <div className="runtime-mobile-switches"><button aria-expanded={drawer === "team"} onClick={() => setDrawer(drawer === "team" ? null : "team")}>团队</button><button aria-expanded={drawer === "inspector"} onClick={() => setDrawer(drawer === "inspector" ? null : "inspector")}>检查器</button></div>
    <div className="task-runtime-layout">
      <div className="runtime-side runtime-team-side" data-open={drawer === "team"}><TeamExplorer store={viewStore} selectedSolverId={selectedSolverId} onSelect={(solverId) => { setSelection({ solverId }); setDrawer(null); }} onDetails={() => { setSelection({ tab: "overview" }); setDrawer(null); }} /></div>
      <main><TaskWorkspaceTabs store={viewStore} tab={selection.tab} selectedSolverId={selectedSolverId} selectedIntentId={selection.intentId} readonly={mode === "replay"} onChanged={refresh} onTab={(tab: RuntimeTab) => setSelection({ tab })} onSolver={(solverId) => setSelection({ solverId })} onIntent={(intentId) => setSelection({ intentId, solverId: viewStore.intentsById[intentId]?.assignedSolverId ?? selection.solverId })} /></main>
      <div className="runtime-side runtime-inspector-side" data-open={drawer === "inspector"}><SolverInspector store={viewStore} solver={selectedSolver ?? null} /></div>
    </div>
    <GlobalActionDock store={viewStore} mode={mode} onRefresh={refresh} onOpenApprovals={() => setSelection({ tab: "approvals" })} onIntervention={() => setInterventionOpen(true)} />
    <InterventionDialog store={store} open={interventionOpen && mode === "runtime"} onClose={() => setInterventionOpen(false)} onSubmitted={refresh} />
  </section>;
}
