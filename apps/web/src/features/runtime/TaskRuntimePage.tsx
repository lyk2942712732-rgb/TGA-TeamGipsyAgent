import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { tga3RuntimeApi } from "../../api/tga3-runtime-control";
import { deleteTask } from "../../api/tga3-tasks";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { TGA3AgentInspector } from "./components/TGA3AgentInspector";
import { TGA3RuntimeGraph } from "./components/TGA3RuntimeGraph";
import { TGA3TaskHeader } from "./components/TGA3TaskHeader";
import { TGA3Workspace, type TGA3WorkspaceTab } from "./components/TGA3Workspace";
import { useTGA3Runtime } from "./use-tga3-runtime";

const VALID_TABS: TGA3WorkspaceTab[] = ["blackboard", "events", "findings", "inputs", "report", "runtime"];

export function TaskRuntimePage({ taskId }: { taskId: string }) {
  const { snapshot, connection, error, refresh } = useTGA3Runtime(taskId);
  const location = useLocation(); const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const requestedTab = params.get("tab") as TGA3WorkspaceTab | null;
  const [tab, setTab] = useState<TGA3WorkspaceTab>(requestedTab && VALID_TABS.includes(requestedTab) ? requestedTab : "blackboard");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(params.get("agent"));
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);
  const [remove, setRemove] = useState(false);

  useEffect(() => {
    if (!snapshot?.agents.length) return;
    if (!selectedAgentId || !snapshot.agents.some((agent) => agent.agent_id === selectedAgentId)) {
      const preferred = snapshot.agents.find((agent) => agent.role === (snapshot.task.state === "scouting" ? "scout" : "worker")) ?? snapshot.agents[0];
      setSelectedAgentId(preferred.agent_id);
    }
  }, [snapshot, selectedAgentId]);

  useEffect(() => {
    const next = new URLSearchParams(location.search);
    if (selectedAgentId) next.set("agent", selectedAgentId); else next.delete("agent");
    next.set("tab", tab);
    if (next.toString() !== params.toString()) navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
  }, [selectedAgentId, tab, location.pathname, location.search, navigate, params]);

  if (!snapshot) return <section className="task-runtime-loading" aria-live="polite"><h1>正在加载 TGA3 任务</h1><p>{error ?? "正在读取任务、Agent、共享黑板和执行事件。"}</p>{error ? <button onClick={refresh}>重试</button> : null}</section>;

  const current = snapshot;
  const selectedAgent = current.agents.find((agent) => agent.agent_id === selectedAgentId) ?? null;
  async function stop() { if (busy) return; setBusy(true); setNotice(null); try { await tga3RuntimeApi.stopTask(taskId); setNotice("停止请求已提交。"); refresh(); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "停止任务失败"); } finally { setBusy(false); } }
  async function confirmDelete() { if (busy) return; setBusy(true); setNotice(null); try { await deleteTask(taskId); navigate("/tasks", { replace: true }); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "删除任务失败"); setBusy(false); setRemove(false); } }

  return <section className="task-runtime-page tga3-runtime-page">
    <TGA3TaskHeader snapshot={snapshot} connection={connection} busy={busy} onRefresh={refresh} onStop={() => void stop()} onDelete={() => setRemove(true)} />
    {error ? <div className="runtime-sync-error" role="alert">实时同步暂时中断：{error}<button onClick={refresh}>重试</button></div> : null}
    {notice ? <div className="runtime-sync-notice" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    <TGA3RuntimeGraph snapshot={snapshot} selectedAgentId={selectedAgentId} onSelectAgent={(id) => { setSelectedAgentId(id); setInspectorOpen(true); }} />
    <div className="workbench-runtime-toggle"><button className="ref-secondary-button" aria-expanded={inspectorOpen} onClick={() => setInspectorOpen(value => !value)}>{inspectorOpen ? "收起 Agent 面板" : "展开 Agent 面板"}</button></div>
    <div className={`workbench-runtime-grid ${inspectorOpen ? "" : "is-wide"}`}>
      <main><TGA3Workspace snapshot={snapshot} tab={tab} selectedAgentId={selectedAgentId} onTab={setTab} onChanged={refresh} /></main>
      {inspectorOpen ? <div className="workbench-inspector"><TGA3AgentInspector snapshot={snapshot} agent={selectedAgent} onChanged={refresh} /></div> : null}
    </div>

    <ConfirmDialog open={remove} title={`删除任务 ${current.task.title}`} description="将停止仍在运行的 Agent，并永久删除该任务的数据库记录、工作区、输入、产物和报告。" confirmLabel="彻底删除" danger busy={busy} onCancel={() => setRemove(false)} onConfirm={() => void confirmDelete()} />
  </section>;
}
