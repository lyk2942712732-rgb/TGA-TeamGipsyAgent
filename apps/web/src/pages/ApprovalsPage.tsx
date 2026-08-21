import { AlertTriangle, Bot, CircleHelp, ExternalLink, Play, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { attentionApi, type AttentionItem } from "../api/tga3-attention";
import { EmptyState } from "../components/ui/EmptyState";

export function ApprovalsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<AttentionItem[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function load() { setError(""); try { setItems(await attentionApi.list()); } catch (reason) { setError(reason instanceof Error ? reason.message : "无法读取待处理事项"); } }
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 5000); return () => clearInterval(timer); }, []);

  async function answer(item: AttentionItem) {
    if (!item.question_id || !answers[item.id]?.trim()) return;
    setBusy(item.id); setError("");
    try { await attentionApi.answer(item.question_id, answers[item.id].trim()); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "回答提交失败"); }
    finally { setBusy(""); }
  }
  async function resume(item: AttentionItem) {
    if (!item.agent_id) return;
    setBusy(item.id); setError("");
    try { await attentionApi.resume(item.task_id, item.agent_id); await load(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "恢复 Agent 失败"); }
    finally { setBusy(""); }
  }

  return <div className="ref-page attention-page">
    <header className="ref-page-head"><div><h1>审批中心</h1><p>集中处理等待用户回答、Agent 暂停以及运行失败等需要人工介入的事项。</p></div><button className="ref-secondary-button" onClick={() => void load()}><RefreshCw size={16} />刷新</button></header>
    {error ? <p className="inline-error" role="alert">{error}</p> : null}
    {!items.length ? <EmptyState title="当前没有待处理事项" description="Supervisor 提问、Agent 暂停或任务失败后会出现在这里。" /> : <div className="attention-list ref-fill">{items.map((item) => <article key={item.id} className={`attention-card ${item.kind}`}>
      <span className="attention-icon">{item.kind === "question" ? <CircleHelp size={21} /> : item.kind === "agent_paused" ? <Bot size={21} /> : <AlertTriangle size={21} />}</span>
      <div className="attention-content"><header><div><h2>{item.title}</h2><p>{item.task_title}</p></div><time>{formatDate(item.created_at)}</time></header><p className="attention-detail">{item.detail}</p><dl><div><dt>任务状态</dt><dd>{item.task_state}</dd></div>{item.agent_id ? <div><dt>来源 Agent</dt><dd>{item.agent_id}</dd></div> : null}</dl>
        {item.kind === "question" ? <div className="attention-answer"><textarea rows={3} value={answers[item.id] ?? ""} onChange={(event) => setAnswers((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="输入回答，提交后问题和答案会写入黑板供所有 Agent 读取。" /><button className="ref-primary-button" disabled={busy === item.id || !answers[item.id]?.trim()} onClick={() => void answer(item)}>提交回答</button></div> : null}
      </div>
      <footer>{item.kind === "agent_paused" ? <button className="ref-primary-button" disabled={busy === item.id} onClick={() => void resume(item)}><Play size={15} />恢复 Agent</button> : null}<button className="ref-secondary-button" onClick={() => navigate(`/tasks/${encodeURIComponent(item.task_id)}/runtime`)}><ExternalLink size={15} />进入任务</button></footer>
    </article>)}</div>}
  </div>;
}

function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN"); }
