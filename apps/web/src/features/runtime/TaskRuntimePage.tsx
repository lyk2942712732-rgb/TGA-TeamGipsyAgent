import { CircleStop, MessageSquareText, ShieldQuestion } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { tga3RuntimeApi } from "../../api/tga3-runtime-control";
import { TGA3AgentInspector } from "./components/TGA3AgentInspector";
import { TGA3AgentRail } from "./components/TGA3AgentRail";
import { TGA3TaskHeader } from "./components/TGA3TaskHeader";
import { TGA3Workspace, type TGA3WorkspaceTab } from "./components/TGA3Workspace";
import { latestPendingQuestion } from "./tga3-view";
import { useTGA3Runtime } from "./use-tga3-runtime";

const VALID_TABS: TGA3WorkspaceTab[] = ["blackboard", "dialogue", "findings", "inputs", "report", "runtime"];

export function TaskRuntimePage({ taskId }: { taskId: string }) {
  const { snapshot, connection, error, refresh } = useTGA3Runtime(taskId);
  const location = useLocation(); const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const requestedTab = params.get("tab") as TGA3WorkspaceTab | null;
  const [tab, setTab] = useState<TGA3WorkspaceTab>(requestedTab && VALID_TABS.includes(requestedTab) ? requestedTab : "blackboard");
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(params.get("agent"));
  const [drawer, setDrawer] = useState<"agents" | "inspector" | null>(null);
  const [conversationNonce, setConversationNonce] = useState(0); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!snapshot?.agents.length) return;
    if (!selectedAgentId || !snapshot.agents.some((agent) => agent.agent_id === selectedAgentId)) {
      const preferred = snapshot.agents.find((agent) => agent.role === "supervisor") ?? snapshot.agents[0];
      setSelectedAgentId(preferred.agent_id);
    }
  }, [snapshot, selectedAgentId]);

  useEffect(() => {
    const next = new URLSearchParams(location.search);
    if (selectedAgentId) next.set("agent", selectedAgentId); else next.delete("agent");
    next.set("tab", tab);
    if (next.toString() !== params.toString()) navigate({ pathname: location.pathname, search: next.toString() }, { replace: true });
  }, [selectedAgentId, tab, location.pathname, location.search, navigate, params]);

  if (!snapshot) return <section className="task-runtime-loading" aria-live="polite"><h1>正在加载 TGA3 任务</h1><p>{error ?? "正在读取任务、Agent、共享黑板和任务对话。"}</p>{error ? <button onClick={refresh}>重试</button> : null}</section>;

  const current = snapshot;
  const selectedAgent = current.agents.find((agent) => agent.agent_id === selectedAgentId) ?? null;
  const question = latestPendingQuestion(current.dialogue, current.task.state);
  const terminal = ["completed", "failed", "cancelled", "stopped"].includes(current.task.state);
  async function stop() { if (busy) return; setBusy(true); setNotice(null); try { await tga3RuntimeApi.stopTask(taskId); setNotice("停止请求已提交。"); refresh(); } catch (reason) { setNotice(reason instanceof Error ? reason.message : "停止任务失败"); } finally { setBusy(false); } }
  function openConversation() { if (!selectedAgentId) setSelectedAgentId(current.agents.find((agent) => agent.role === "supervisor")?.agent_id ?? current.agents[0]?.agent_id ?? null); setConversationNonce((value) => value + 1); setDrawer("inspector"); }

  return <section className="task-runtime-page tga3-runtime-page">
    <TGA3TaskHeader snapshot={snapshot} connection={connection} busy={busy} onRefresh={refresh} onStop={() => void stop()} />
    {error ? <div className="runtime-sync-error" role="alert">实时同步暂时中断：{error}<button onClick={refresh}>重试</button></div> : null}
    {notice ? <div className="runtime-sync-notice" role="status">{notice}<button onClick={() => setNotice(null)}>关闭</button></div> : null}
    <div className="runtime-mobile-switches"><button aria-expanded={drawer === "agents"} onClick={() => setDrawer(drawer === "agents" ? null : "agents")}>Agent</button><button aria-expanded={drawer === "inspector"} onClick={() => setDrawer(drawer === "inspector" ? null : "inspector")}>Agent 面板</button></div>
    <div className="tga3-runtime-layout">
      <div className="tga3-runtime-side tga3-agent-side" data-open={drawer === "agents"}><TGA3AgentRail agents={snapshot.agents} selectedAgentId={selectedAgentId} onSelect={(agentId) => { setSelectedAgentId(agentId); setDrawer(null); }} /></div>
      <main><TGA3Workspace snapshot={snapshot} tab={tab} selectedAgentId={selectedAgentId} onTab={setTab} /></main>
      <div className="tga3-runtime-side tga3-inspector-side" data-open={drawer === "inspector"}><TGA3AgentInspector snapshot={snapshot} agent={selectedAgent} openConversationNonce={conversationNonce} onChanged={refresh} /></div>
    </div>
    <footer className="tga3-action-dock">
      <button type="button" onClick={openConversation}><MessageSquareText size={16} />给 Agent 添加提示</button>
      <button type="button" className={question ? "needs-attention" : ""} onClick={() => navigate(`/approvals?task_id=${encodeURIComponent(taskId)}`)}><ShieldQuestion size={16} />{question ? "处理待回答问题" : "Q&A 中心"}</button>
      {!terminal ? <button type="button" className="danger" disabled={busy} onClick={() => void stop()}><CircleStop size={16} />停止任务</button> : null}
    </footer>
  </section>;
}
