import { Download, File, FileText, Flag, Grid2X2, Lightbulb, List, Maximize2, MessageSquareText, Search, ShieldCheck, X } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import type { TGA3BoardEntry, TGA3DialogueMessage, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { BOARD_KIND_LABELS, DIALOGUE_KIND_LABELS, bodyText, formatTime, listStrings } from "../tga3-view";
import { TGA3RuntimeGraph } from "./TGA3RuntimeGraph";

export type TGA3WorkspaceTab = "blackboard" | "dialogue" | "findings" | "inputs" | "report" | "runtime";
const TABS: Array<[TGA3WorkspaceTab, string]> = [["blackboard", "共享黑板"], ["dialogue", "任务对话"], ["findings", "Findings"], ["inputs", "输入与文件"], ["report", "报告"], ["runtime", "运行图"]];

export function TGA3Workspace({ snapshot, tab, selectedAgentId, onTab }: { snapshot: TGA3RuntimeSnapshot; tab: TGA3WorkspaceTab; selectedAgentId: string | null; onTab: (tab: TGA3WorkspaceTab) => void }) {
  const effectiveTab = TABS.some(([value]) => value === tab) ? tab : "blackboard";
  return <section className="tga3-workspace" aria-label="TGA3 任务工作区">
    <header><div><span>SHARED TASK STATE</span><h2>任务工作区</h2></div><p>黑板是 Agent 之间唯一的协作通道</p></header>
    <div className="tga3-workspace-tabs" role="tablist" aria-label="任务数据">
      {TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={effectiveTab === value} onClick={() => onTab(value)}>{label}{tabCount(value, snapshot) ? <small>{tabCount(value, snapshot)}</small> : null}</button>)}
    </div>
    <div className="tga3-workspace-panel" role="tabpanel">
      {effectiveTab === "blackboard" ? <BlackboardPanel entries={snapshot.blackboard} /> : null}
      {effectiveTab === "dialogue" ? <DialoguePanel messages={snapshot.dialogue} selectedAgentId={selectedAgentId} /> : null}
      {effectiveTab === "findings" ? <FindingsPanel entries={snapshot.blackboard} /> : null}
      {effectiveTab === "inputs" ? <InputsPanel entries={snapshot.blackboard} /> : null}
      {effectiveTab === "report" ? <ReportPanel snapshot={snapshot} /> : null}
      {effectiveTab === "runtime" ? <TGA3RuntimeGraph snapshot={snapshot} /> : null}
    </div>
  </section>;
}

function BlackboardPanel({ entries }: { entries: TGA3BoardEntry[] }) {
  const [kind, setKind] = useState(""); const [query, setQuery] = useState("");
  const [view, setView] = useState<"tiles" | "details">("details");
  const [focused, setFocused] = useState<TGA3BoardEntry | null>(null);
  const visible = useMemo(() => [...entries].reverse().filter((entry) => (!kind || entry.kind === kind) && (!query.trim() || `${entry.topic} ${bodyText(entry)} ${entry.actor.display_name}`.toLowerCase().includes(query.trim().toLowerCase()))), [entries, kind, query]);
  return <div className="tga3-board-panel">
    <div className="tga3-panel-toolbar tga3-board-toolbar">
      <label><Search size={14} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索黑板内容" /></label>
      <div className="tga3-board-toolbar-actions">
        <select aria-label="黑板类型" value={kind} onChange={(event) => setKind(event.target.value)}><option value="">全部类型</option>{Object.entries(BOARD_KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
        <div className="tga3-view-switch" role="group" aria-label="共享黑板展示方式">
          <button type="button" aria-pressed={view === "tiles"} onClick={() => setView("tiles")}><Grid2X2 size={14} />方块</button>
          <button type="button" aria-pressed={view === "details"} onClick={() => setView("details")}><List size={14} />详情</button>
        </div>
      </div>
    </div>
    {visible.length ? view === "tiles"
      ? <div className="tga3-board-tile-grid">{visible.map((entry) => <BoardTile key={entry.id} entry={entry} onOpen={() => setFocused(entry)} />)}</div>
      : <div className="tga3-entry-list">{visible.map((entry) => <BoardCard key={entry.id} entry={entry} />)}</div>
      : <Empty icon={<ShieldCheck />} title="黑板尚无匹配内容" detail="场景提示、用户输入、Supervisor 建议、Intel、Finding、Q&A 和最终候选会按顺序写入这里。" />}
    {focused ? <BoardFocus entry={focused} onClose={() => setFocused(null)} /> : null}
  </div>;
}

function BoardTile({ entry, onOpen, focused = false }: { entry: TGA3BoardEntry; onOpen?: () => void; focused?: boolean }) {
  return <button type="button" className={`tga3-board-tile kind-${entry.kind}`} data-focused={focused} aria-label={`查看黑板 #${entry.seq}：${BOARD_KIND_LABELS[entry.kind]}`} onClick={onOpen}>
    <header><span>#{entry.seq} · {BOARD_KIND_LABELS[entry.kind]}</span><Maximize2 size={14} /></header>
    <strong>{boardTileTitle(entry)}</strong>
    <p>{bodyText(entry) || "点击查看完整内容"}</p>
    <footer><span>{entry.actor.display_name}</span><em>{entry.topic}</em></footer>
  </button>;
}

function BoardFocus({ entry, onClose }: { entry: TGA3BoardEntry; onClose: () => void }) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return createPortal(<div className="tga3-finding-focus-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="tga3-board-focus-composition" role="dialog" aria-modal="true" aria-label={`黑板 #${entry.seq} 详情`}>
      <BoardTile entry={entry} focused />
      <article className="tga3-board-message">
        <button type="button" className="tga3-finding-close" aria-label="关闭黑板详情" onClick={onClose}><X size={18} /></button>
        <BoardCard entry={entry} />
      </article>
    </div>
  </div>, document.body);
}

function BoardCard({ entry }: { entry: TGA3BoardEntry }) { return <article className={`tga3-board-card kind-${entry.kind}`}><header><span>#{entry.seq} · {BOARD_KIND_LABELS[entry.kind]}</span><small>{formatTime(entry.created_at)}</small></header><div className="tga3-entry-author"><b>{entry.actor.display_name}</b><span>{entry.actor.agent_id}</span><em>{entry.topic}</em></div><BoardBody entry={entry} /></article>; }
function BoardBody({ entry }: { entry: TGA3BoardEntry }) {
  const body = entry.body;
  if (entry.kind === "supervisor_advice") return <><p>{String(body.advice ?? "")}</p><Meta label="建议对象" values={listStrings(body.addressed_to)} /></>;
  if (entry.kind === "intel" || entry.kind === "finding") return <><h3>{String(body.claim ?? entry.topic)}</h3>{body.detail ? <p>{String(body.detail)}</p> : null}</>;
  if (entry.kind === "qa") return <><dl className="tga3-qa"><div><dt>问题</dt><dd>{String(body.question ?? "")}</dd></div><div><dt>回答</dt><dd>{String(body.answer ?? "")}</dd></div></dl><Meta label="问题来源" values={[String(body.origin_agent_id ?? body.origin_type ?? "supervisor")]} /></>;
  if (entry.kind === "final_candidate") return <><h3><Flag size={16} />{String(body.conclusion ?? "最终候选")}</h3><p>{String(body.rationale ?? "")}</p><Meta label="引用 Finding" values={listStrings(body.finding_ids)} /></>;
  if (entry.kind === "user_file") return <><h3><File size={16} />{String(body.name ?? "用户文件")}</h3><p>{String(body.media_type ?? "")}</p><code>{String(body.sha256 ?? "")}</code></>;
  return <><p>{String(body.text ?? bodyText(entry))}</p><Meta label="发送给" values={listStrings(body.addressed_to)} /></>;
}

function DialoguePanel({ messages, selectedAgentId }: { messages: TGA3DialogueMessage[]; selectedAgentId: string | null }) {
  const [scope, setScope] = useState<"all" | "selected">("all");
  const visible = [...messages].reverse().filter((message) => scope === "all" || !selectedAgentId || message.channel_agent_id === selectedAgentId || message.actor.agent_id === selectedAgentId || message.payload.agent_id === selectedAgentId);
  return <div className="tga3-dialogue-panel"><div className="tga3-panel-toolbar"><div className="tga3-segment"><button aria-pressed={scope === "all"} onClick={() => setScope("all")}>全部通道</button><button aria-pressed={scope === "selected"} disabled={!selectedAgentId} onClick={() => setScope("selected")}>当前 Agent</button></div><small>展示持久化摘要与动作，不展示隐藏思维链</small></div>{visible.length ? <div className="tga3-dialogue-list">{visible.map((message) => <DialogueCard key={message.id} message={message} />)}</div> : <Empty icon={<MessageSquareText />} title="暂无任务对话" detail="Agent 状态、进度摘要、工具动作、Supervisor 提问和用户提示会出现在这里。" />}</div>;
}
export function DialogueCard({ message, compact = false }: { message: TGA3DialogueMessage; compact?: boolean }) { return <article className={`tga3-dialogue-card kind-${message.kind}`} data-compact={compact}><span className="tga3-dialogue-dot" /><div><header><b>{message.actor.display_name}</b><em>{DIALOGUE_KIND_LABELS[message.kind]}</em><small>{formatTime(message.created_at)}</small></header><p>{message.text}</p>{!compact ? <footer>通道：{message.channel_agent_id} · #{message.seq}</footer> : null}</div></article>; }

function FindingsPanel({ entries }: { entries: TGA3BoardEntry[] }) {
  const findings = entries.filter((entry) => entry.kind === "finding"); const finals = entries.filter((entry) => entry.kind === "final_candidate");
  const [view, setView] = useState<"tiles" | "details">("tiles");
  const [focused, setFocused] = useState<TGA3BoardEntry | null>(null);
  const ordered = [...findings].reverse();
  return <div className="tga3-findings-panel">
    <section>
      <header className="tga3-section-heading tga3-findings-heading">
        <div><ShieldCheck size={17} /><h3>已验证 Findings</h3><small>{findings.length}</small></div>
        <div className="tga3-view-switch" role="group" aria-label="Finding 展示方式">
          <button type="button" aria-pressed={view === "tiles"} onClick={() => setView("tiles")}><Grid2X2 size={14} />方块</button>
          <button type="button" aria-pressed={view === "details"} onClick={() => setView("details")}><List size={14} />详情</button>
        </div>
      </header>
      {findings.length ? view === "tiles"
        ? <div className="tga3-finding-tile-grid">{ordered.map((entry) => <FindingTile key={entry.id} entry={entry} onOpen={() => setFocused(entry)} />)}</div>
        : <div className="tga3-finding-detail-list">{ordered.map((entry) => <BoardCard key={entry.id} entry={entry} />)}</div>
        : <Empty icon={<ShieldCheck />} title="尚无 Finding" detail="Worker 只有在关联对象可定位并通过写入关隘后，才能把 Finding 发布到黑板。" />}
    </section>
    {finals.length ? <section className="tga3-final-candidates"><header><Flag size={17} /><h3>最终候选</h3></header>{[...finals].reverse().map((entry) => <BoardCard key={entry.id} entry={entry} />)}</section> : null}
    {focused ? <FindingFocus entry={focused} onClose={() => setFocused(null)} /> : null}
  </div>;
}

function FindingTile({ entry, onOpen, focused = false }: { entry: TGA3BoardEntry; onOpen?: () => void; focused?: boolean }) {
  const claim = String(entry.body.claim ?? entry.topic);
  const detail = String(entry.body.detail ?? "");
  return <button type="button" className="tga3-finding-tile" data-focused={focused} aria-label={`查看 Finding #${entry.seq}：${claim}`} onClick={onOpen}>
    <header><span>#{entry.seq}</span><Maximize2 size={14} /></header>
    <strong>{claim}</strong>
    <p>{detail || "点击查看完整 Finding"}</p>
    <footer><span>{entry.actor.display_name}</span><em>{entry.topic}</em></footer>
  </button>;
}

function FindingFocus({ entry, onClose }: { entry: TGA3BoardEntry; onClose: () => void }) {
  useEffect(() => {
    function closeOnEscape(event: KeyboardEvent) { if (event.key === "Escape") onClose(); }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  const claim = String(entry.body.claim ?? entry.topic);
  const detail = String(entry.body.detail ?? "");
  return createPortal(<div className="tga3-finding-focus-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="tga3-finding-focus-composition" role="dialog" aria-modal="true" aria-label={`Finding #${entry.seq} 详情`}>
      <FindingTile entry={entry} focused />
      <article className="tga3-finding-message">
        <button type="button" className="tga3-finding-close" aria-label="关闭 Finding 详情" onClick={onClose}><X size={18} /></button>
        <header><div><b>{entry.actor.display_name}</b><span>{entry.actor.agent_id}</span></div><time>{formatTime(entry.created_at)}</time></header>
        <h3>{claim}</h3>
        {detail ? <p>{detail}</p> : <p className="tga3-finding-no-detail">该 Finding 没有补充详情。</p>}
        <footer><span>Finding #{entry.seq}</span><em>{entry.topic}</em><code>{entry.id}</code></footer>
      </article>
    </div>
  </div>, document.body);
}
function InputsPanel({ entries }: { entries: TGA3BoardEntry[] }) {
  const prompts = entries.filter((entry) => entry.kind === "user_prompt"); const files = entries.filter((entry) => entry.kind === "user_file");
  return <div className="tga3-inputs-panel"><section><header className="tga3-section-heading"><div><Lightbulb size={17} /><h3>提示词</h3></div><small>{prompts.length}</small></header>{prompts.map((entry) => <BoardCard key={entry.id} entry={entry} />)}</section><section><header className="tga3-section-heading"><div><File size={17} /><h3>用户文件</h3></div><small>{files.length}</small></header>{files.length ? files.map((entry) => <BoardCard key={entry.id} entry={entry} />) : <Empty icon={<File />} title="没有用户文件" detail="任务创建或后续提示中上传的文件会登记在这里。" />}</section></div>;
}
function ReportPanel({ snapshot }: { snapshot: TGA3RuntimeSnapshot }) {
  const finals = snapshot.blackboard.filter((entry) => entry.kind === "final_candidate"); const ready = snapshot.task.state === "completed";
  return <div className="tga3-report-panel"><FileText size={34} /><span>REPORTER</span><h3>{ready ? "Markdown Writeup 已生成" : finals.length ? "Reporter 正在固定黑板快照并生成报告" : "等待最终候选"}</h3><p>{ready ? "报告只基于 final_candidate 触发时固定的黑板内容生成。" : "Worker 发布引用既有 Finding 的 final_candidate 后，Reporter 才会被唤醒。"}</p>{ready ? <a className="ref-primary-button" href={tga3RuntimeApi.reportUrl(snapshot.task.id)} target="_blank" rel="noreferrer"><Download size={15} />下载 writeup.md</a> : null}</div>;
}
function Meta({ label, values }: { label: string; values: string[] }) { return values.length ? <footer className="tga3-entry-meta"><span>{label}</span>{values.map((value, index) => <em key={`${value}-${index}`}>{value}</em>)}</footer> : null; }
function Empty({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) { return <div className="tga3-empty">{icon}<h3>{title}</h3><p>{detail}</p></div>; }
function boardTileTitle(entry: TGA3BoardEntry): string {
  if (entry.kind === "intel" || entry.kind === "finding") return String(entry.body.claim ?? entry.topic);
  if (entry.kind === "supervisor_advice") return "Supervisor 建议";
  if (entry.kind === "final_candidate") return String(entry.body.conclusion ?? "最终候选");
  if (entry.kind === "user_file") return String(entry.body.name ?? "用户文件");
  if (entry.kind === "qa") return String(entry.body.question ?? "Q&A");
  return entry.topic || "用户提示";
}
function tabCount(tab: TGA3WorkspaceTab, snapshot: TGA3RuntimeSnapshot) { if (tab === "blackboard") return snapshot.blackboard.length; if (tab === "dialogue") return snapshot.dialogue.length; if (tab === "findings") return snapshot.blackboard.filter((entry) => ["finding", "final_candidate"].includes(entry.kind)).length; if (tab === "inputs") return snapshot.blackboard.filter((entry) => ["user_prompt", "user_file"].includes(entry.kind)).length; if (tab === "report") return snapshot.task.state === "completed" ? 1 : 0; return 0; }
