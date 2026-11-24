import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { tga3RuntimeApi, type RestartTaskPayload } from "../../api/tga3-runtime-control";
import { deleteTask } from "../../api/tga3-tasks";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { AOEFinishConfirmDialog } from "./components/AOEFinishConfirmDialog";
import { RestartTaskDialog } from "./components/RestartTaskDialog";
import { TGA3AgentInspector } from "./components/TGA3AgentInspector";
import { TGA3RuntimeGraph } from "./components/TGA3RuntimeGraph";
import { TGA3TaskHeader } from "./components/TGA3TaskHeader";
import { TGA3Workspace, type TGA3WorkspaceTab } from "./components/TGA3Workspace";
import { useTGA3Runtime } from "./use-tga3-runtime";
import { AOEWorkspace } from "./components/AOEWorkspace";

const VALID_TABS: TGA3WorkspaceTab[] = ["blackboard", "events", "findings", "inputs", "report"];

export function TaskRuntimePage({ taskId }: { taskId: string }) {
  const { snapshot, connection, error, refresh } = useTGA3Runtime(taskId);
  const location = useLocation(); const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const requestedTab = params.get("tab") as TGA3WorkspaceTab | null;
  const [ordinaryTab, setOrdinaryTab] = useState<TGA3WorkspaceTab>(requestedTab && VALID_TABS.includes(requestedTab) ? requestedTab : "blackboard");
  const tab = snapshot?.aoe ? (requestedTab && VALID_TABS.includes(requestedTab) ? requestedTab : "blackboard") : ordinaryTab;
  function setTab(value: TGA3WorkspaceTab) {
    if (!snapshot?.aoe) { setOrdinaryTab(value); return; }
    const next = new URLSearchParams(location.search);
    next.set("tab", value);
    navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
  }
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(params.get("agent"));
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  const [remove, setRemove] = useState(false);
  const [restartOpen, setRestartOpen] = useState(false);
  const [restartError, setRestartError] = useState<string | null>(null);
  const [finishMinimizedPromptAt, setFinishMinimizedPromptAt] = useState<number | null>(null);
  const [finishBusy, setFinishBusy] = useState(false);
  const [finishError, setFinishError] = useState<string | null>(null);

  const pendingFinishConfirm =
    snapshot?.aoe?.finish_confirm?.status === "pending" &&
    !["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state)
      ? snapshot.aoe.finish_confirm
      : null;
  const finishPromptKey = pendingFinishConfirm?.prompted_at ?? pendingFinishConfirm?.wave_question_count ?? 0;
  const finishDialogOpen = Boolean(pendingFinishConfirm) && finishMinimizedPromptAt !== finishPromptKey;

  useEffect(() => {
    if (!pendingFinishConfirm) {
      setFinishMinimizedPromptAt(null);
      setFinishError(null);
    }
  }, [pendingFinishConfirm]);

  useEffect(() => {
    if (!snapshot) return;
    const isTerminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
    if (params.get("restart") === "1" && isTerminal) {
      setRestartOpen(true);
      setRestartError(null);
      const next = new URLSearchParams(location.search);
      next.delete("restart");
      navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
    }
  }, [snapshot, params, location.pathname, location.search, navigate]);

  useEffect(() => {
    if (!snapshot?.agents.length) return;
    if (!selectedAgentId || !snapshot.agents.some((agent) => agent.agent_id === selectedAgentId)) {
      const preferred = snapshot.agents.find((agent) => agent.role === (snapshot.task.state === "scouting" ? "scout" : "worker")) ?? snapshot.agents[0];
      setSelectedAgentId(preferred.agent_id);
    }
  }, [snapshot, selectedAgentId]);

  useEffect(() => {
    if (snapshot?.aoe) return;
    const next = new URLSearchParams(location.search);
    if (selectedAgentId) next.set("agent", selectedAgentId); else next.delete("agent");
    next.set("tab", tab);
    if (next.toString() !== params.toString()) navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
  }, [selectedAgentId, tab, location.pathname, location.search, navigate, params, snapshot?.aoe]);

  if (!snapshot) return <section className="task-runtime-loading" aria-live="polite"><h1>正在加载 TGA3 任务</h1><p>{error ?? "正在读取任务、Agent、共享黑板和执行事件。"}</p>{error ? <button onClick={refresh}>重试</button> : null}</section>;

  const current = snapshot;
  const selectedAgent = current.agents.find((agent) => agent.agent_id === selectedAgentId) ?? null;
  async function stop() { if (busy) return; setBusy(true); setNotice(null); try { await tga3RuntimeApi.stopTask(taskId); setNotice("停止请求已提交。"); refresh(); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "停止任务失败"); } finally { setBusy(false); } }
  async function confirmDelete() { if (busy) return; setBusy(true); setNotice(null); try { await deleteTask(taskId); navigate("/tasks", { replace: true }); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "删除任务失败"); setBusy(false); setRemove(false); } }
  async function confirmRestart(payload: RestartTaskPayload) {
    if (busy) return;
    setBusy(true);
    setRestartError(null);
    try {
      const res = await tga3RuntimeApi.restartTask(taskId, payload);
      setRestartOpen(false);
      const stageLabel = res.resumed_from === "scout" ? "Scout 阶段" : res.resumed_from === "reporter" ? "Reporter 报告阶段" : "Workers 解题阶段";
      const fallbackNote = res.fallback_models?.length ? `（已自动回退为默认模型：${res.fallback_models.join("、")}）` : "";
      setNotice(`任务已原地重启（第 ${res.restart_count} 次运行），将从 ${stageLabel}继续推进${fallbackNote}。`);
      refresh();
    } catch (reason) {
      setRestartError(reason instanceof Error ? reason.message : "重启任务失败");
    } finally {
      setBusy(false);
    }
  }
  function cloneToNew() {
    setRestartOpen(false);
    navigate(`/tasks/new?clone=${encodeURIComponent(taskId)}`);
  }
  async function handleFinishContinue(autoStandbyFuture: boolean) {
    if (finishBusy) return;
    setFinishBusy(true);
    setFinishError(null);
    try {
      const res = await tga3RuntimeApi.submitFinishDecision(taskId, "continue_standby", autoStandbyFuture);
      setNotice(
        res.scout_watch?.mode === "sentinel" && res.scout_watch?.enabled
          ? "任务保持存活，零 Token 哨兵持续站岗候题。"
          : "任务保持存活待命，可随时手动唤醒 Scout 巡检新题。",
      );
      refresh();
    } catch (reason) {
      setFinishError(reason instanceof Error ? reason.message : "提交继续站岗指令失败");
    } finally {
      setFinishBusy(false);
    }
  }
  async function handleFinishStop(skipReports: boolean) {
    if (finishBusy) return;
    setFinishBusy(true);
    setFinishError(null);
    try {
      await tga3RuntimeApi.submitFinishDecision(taskId, skipReports ? "finish_now" : "finish_after_reports");
      setNotice(skipReports ? "已确认立即结束任务。" : "已确认结束任务，待剩余逐题报告完成后自动归档。");
      refresh();
    } catch (reason) {
      setFinishError(reason instanceof Error ? reason.message : "提交结束任务指令失败");
    } finally {
      setFinishBusy(false);
    }
  }

  return <section className={`task-runtime-page tga3-runtime-page${snapshot.aoe ? " aoe-runtime-page" : ""}`}>
    <TGA3TaskHeader
      snapshot={snapshot}
      connection={connection}
      busy={busy}
      onRefresh={refresh}
      onStop={() => void stop()}
      onRestart={() => { setRestartError(null); setRestartOpen(true); }}
      onClone={cloneToNew}
      onDelete={() => setRemove(true)}
    />
    {pendingFinishConfirm && !finishDialogOpen ? (
      <div className="aoe-finish-banner" role="status">
        <span>
          当前全部题目已进入已解出或已放弃状态，等待确认是否继续由 Scout 站岗候题。
        </span>
        <button
          type="button"
          className="ref-primary-button"
          onClick={() => setFinishMinimizedPromptAt(null)}
        >
          打开结束确认窗
        </button>
      </div>
    ) : null}
    {error ? <div className="runtime-sync-error" role="alert">实时同步暂时中断：{error}<button onClick={refresh}>重试</button></div> : null}
    {notice ? <div className="runtime-sync-notice" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    <TGA3RuntimeGraph snapshot={snapshot} selectedAgentId={selectedAgentId} onSelectAgent={(id) => { setSelectedAgentId(id); setInspectorOpen(true); }} />
    <div className="workbench-runtime-toggle"><button className="ref-secondary-button" aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(value => !value)}>{inspectorOpen ? "收起 Agent 面板" : "展开 Agent 面板"}</button></div>
    <div className={`workbench-runtime-grid ${inspectorOpen ? "" : "is-wide"}`}>
      <main>{snapshot.aoe ? <AOEWorkspace snapshot={snapshot} tab={tab} selectedAgentId={selectedAgentId} onTab={setTab} onChanged={refresh} /> : <TGA3Workspace snapshot={snapshot} tab={tab} selectedAgentId={selectedAgentId} onTab={setTab} onChanged={refresh} />}</main>
      {inspectorOpen ? <div className="workbench-inspector"><TGA3AgentInspector snapshot={snapshot} agent={selectedAgent} onChanged={refresh} /></div> : null}
    </div>

    <RestartTaskDialog
      open={restartOpen}
      snapshot={current}
      busy={busy}
      error={restartError}
      onConfirm={(payload) => void confirmRestart(payload)}
      onCloneNew={cloneToNew}
      onCancel={() => setRestartOpen(false)}
    />
    <ConfirmDialog open={remove} title={`删除任务 ${current.task.title}`} description="将停止仍在运行的 Agent，并永久删除该任务的数据库记录、工作区、输入、产物和报告。" confirmLabel="彻底删除" danger busy={busy} onCancel={() => setRemove(false)} onConfirm={() => void confirmDelete()} />
    {pendingFinishConfirm ? (
      <AOEFinishConfirmDialog
        open={finishDialogOpen}
        taskTitle={current.task.title}
        confirm={pendingFinishConfirm}
        watch={current.aoe?.scout_watch ?? null}
        busy={finishBusy}
        error={finishError}
        onContinueStandby={(autoStandbyFuture) => void handleFinishContinue(autoStandbyFuture)}
        onFinish={(skipReports) => void handleFinishStop(skipReports)}
        onMinimize={() => setFinishMinimizedPromptAt(finishPromptKey)}
      />
    ) : null}
  </section>;
}
