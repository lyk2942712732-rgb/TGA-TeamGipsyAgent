import { Bot, CirclePause, CirclePlay, RefreshCw, ShieldCheck, Square } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { ProviderProtocol } from "../../../api/tga3-config";
import { tga3RuntimeApi } from "../../../api/tga3-runtime-control";
import { fetchAgentModelOptions } from "../../../api/tga3-tasks";
import { ModelPicker } from "../../../components/ModelPicker";
import type { TaskMode } from "../../../modes";
import type { TGA3Agent, TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { attemptLabel, formatElapsed, workerDifficultyAverages, workerRecords } from "../aoe-view";
import {
  agentStateLabel,
  BOARD_KIND_LABELS,
  bodyText,
  formatTime,
  protocolLabel,
  roleLabel,
  sdkLabel,
  stateLabel,
  stateTone,
} from "../tga3-view";
import { EventCard } from "./TGA3Workspace";

type InspectorTab = "overview" | "activity" | "control" | "record";
const TABS: Array<[InspectorTab, string]> = [
  ["overview", "概览"],
  ["activity", "活动"],
  ["control", "模型与控制"],
];

function shortSdk(sdk: string) {
  return ({ codex_cli: "CDX", claude_cli: "CLD", opencode_cli: "OCD", qwen_cli: "QWN" } as Record<string, string>)[sdk] ?? sdk;
}

export function TGA3AgentInspector({
  snapshot,
  agent,
  onChanged,
}: {
  snapshot: TGA3RuntimeSnapshot;
  agent: TGA3Agent | null;
  onChanged: () => void;
}) {
  const [tab, setTab] = useState<InspectorTab>("overview");
  if (!agent) {
    return (
      <aside className="insp-root">
        <div className="tga3-empty">
          <Bot size={20} />
          <h3>未选择 Agent</h3>
          <p>从上方舰队条或拓扑画布选择一个 Agent 查看状态和活动。</p>
        </div>
      </aside>
    );
  }
  const tabs: typeof TABS = snapshot.aoe && agent.role === "worker" ? [...TABS, ["record", "战绩"]] : TABS;
  const activeTab = tabs.some(([value]) => value === tab) ? tab : "overview";
  const tone = stateTone(agent.actual_state);

  return (
    <aside className="insp-root tga3-agent-inspector">
      <div className="proto-section-bar">
        <span className="proto-section-title truncate">INSPECTOR · {agent.display_name}</span>
        <span className={`cli-badge is-${agent.sdk}`}>{shortSdk(agent.sdk)}</span>
      </div>

      <div className="insp-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: "14px", fontWeight: 600 }}>{agent.display_name}</h2>
            <div className="mono subtle truncate" style={{ fontSize: "11px" }}>
              {roleLabel(agent)} · {agent.model_name ?? agent.model_id}
            </div>
          </div>
          <span className={`ref-chip tone-${tone === "active" ? "accent" : tone === "success" ? "ok" : tone === "waiting" ? "warn" : tone === "danger" ? "danger" : "muted"}`}>
            {agentStateLabel(agent)}
          </span>
        </div>
      </div>

      <div className="insp-tabs" role="tablist">
        {tabs.map(([value, label]) => (
          <button key={value} type="button" role="tab" aria-selected={activeTab === value} onClick={() => setTab(value)}>
            {label}
          </button>
        ))}
      </div>

      <div className="insp-scroll">
        {activeTab === "overview" ? <Overview snapshot={snapshot} agent={agent} /> : null}
        {activeTab === "activity" ? <Activity snapshot={snapshot} agent={agent} /> : null}
        {activeTab === "control" ? <Controls snapshot={snapshot} agent={agent} onChanged={onChanged} /> : null}
        {activeTab === "record" ? <RecordView snapshot={snapshot} agent={agent} /> : null}
      </div>
    </aside>
  );
}

function Overview({ snapshot, agent }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent }) {
  const tools = agentTools(agent);
  const now = useElapsedNow();
  const records = workerRecords(snapshot.aoe?.questions ?? [], agent.agent_id, now);
  const averages = workerDifficultyAverages(snapshot.aoe?.questions ?? [], agent.agent_id);
  const workers = snapshot.agents.filter((a) => a.role === "worker");

  return (
    <div>
      {snapshot.aoe && agent.role === "worker" ? (
        <>
          <section className="insp-section">
            <h3 className="caps mono subtle" style={{ margin: 0 }}>解题进度</h3>
            <div className="insp-kv">
              <span>已解出</span>
              <strong style={{ color: "var(--c-success)" }}>
                {records.filter((r) => r.successful || r.assisted).length} 道
              </strong>
            </div>
            <div className="insp-kv">
              <span>未成功解出</span>
              <strong>{records.filter((r) => !r.successful && !r.assisted).length} 道</strong>
            </div>
            <div className="insp-kv">
              <span>正在解题</span>
              <strong>
                {snapshot.aoe.questions
                  .filter((q) => q.status === "running" && q.current_agents.includes(agent.agent_id))
                  .map((q) => q.title)
                  .join("、") || "暂无"}
              </strong>
            </div>
          </section>
          <section className="insp-section">
            <h3 className="caps mono subtle" style={{ margin: 0 }}>各难度解题平均耗时</h3>
            {averages.length ? (
              averages.map((row) => (
                <div className="insp-kv" key={row.difficulty}>
                  <span>难度：{row.difficulty}</span>
                  <strong>
                    {formatElapsed(row.averageSeconds)} · {row.count} 道
                  </strong>
                </div>
              ))
            ) : (
              <div className="insp-kv">
                <span>有效样本</span>
                <span className="subtle">暂无耗时完整的已解出题目</span>
              </div>
            )}
          </section>
        </>
      ) : null}

      <div className="insp-section">
        <div className="caps mono subtle">运行遥测</div>
        <div className="insp-kv">
          <span>角色</span>
          <strong>{roleLabel(agent)}</strong>
        </div>
        <div className="insp-kv">
          <span>执行后端</span>
          <strong>{sdkLabel(agent.sdk)}</strong>
        </div>
        <div className="insp-kv">
          <span>运行位置</span>
          <strong>{agent.runtime_location === "container" ? "隔离容器" : "主服务"}</strong>
        </div>
        <div className="insp-kv">
          <span>供应商 / 模型</span>
          <strong>
            {agent.provider_id} / {agent.model_id}
          </strong>
        </div>
        <div className="insp-kv">
          <span>协议</span>
          <strong>{protocolLabel(agent.protocol)}</strong>
        </div>
        <div className="insp-kv">
          <span>期望 / 实际状态</span>
          <strong>
            {stateLabel(agent.desired_state)} / {agentStateLabel(agent)}
          </strong>
        </div>
        {agent.role === "worker" && agent.metadata?.blackboard_cursor !== undefined ? (
          <div className="insp-kv">
            <span>黑板已读游标</span>
            <strong>#{agent.metadata.blackboard_cursor}</strong>
          </div>
        ) : null}
        {agent.runtime_location === "container" ? (
          <div className="insp-kv">
            <span>Container</span>
            <code>{agent.container_id || "尚未分配"}</code>
          </div>
        ) : null}
      </div>

      {snapshot.aoe && workers.length > 1 ? (
        <div className="insp-section">
          <div className="caps mono subtle">舰队贡献对比</div>
          {workers.map((w) => {
            const wins = workerRecords(snapshot.aoe?.questions ?? [], w.agent_id, now).filter(
              (r) => r.successful || r.assisted,
            ).length;
            const tok = w.metadata?.token_usage?.reported
              ? `${(w.metadata.token_usage.total_tokens / 1000).toFixed(1)}k`
              : "—";
            return (
              <div key={w.agent_id} style={{ display: "grid", gap: 3 }}>
                <div className="insp-kv" style={{ fontSize: "11px" }}>
                  <span className="mono">{w.display_name}</span>
                  <strong className="mono">
                    {wins} 解 · {tok}
                  </strong>
                </div>
                <div className="proto-progress">
                  <div
                    className={`proto-progress-fill${w.agent_id === agent.agent_id ? "" : " is-ok"}`}
                    style={{ width: `${Math.min(100, Math.max(12, wins * 25))}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="insp-section">
        <div className="caps mono subtle">可用工具</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {tools.map((tool) => (
            <span key={tool} className="ref-chip">
              {tool}
            </span>
          ))}
        </div>
      </div>

      {agent.actual_state === "failed" && agent.last_error ? (
        <div className="insp-section" role="alert">
          <strong style={{ color: "var(--c-danger)", fontSize: "12px" }}>最近错误</strong>
          <p className="inline-error">{agent.last_error}</p>
          <small className="mono subtle">{formatTime(agent.updated_at)}</small>
        </div>
      ) : null}
    </div>
  );
}

function RecordView({ snapshot, agent }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent }) {
  const now = useElapsedNow();
  const records = workerRecords(snapshot.aoe?.questions ?? [], agent.agent_id, now);
  const wins = records.filter((r) => r.successful || r.assisted);
  const unsuccessful = records.filter((r) => !r.successful && !r.assisted);
  const link = (id: string) =>
    `/tasks/${encodeURIComponent(snapshot.task.id)}/runtime?question=${encodeURIComponent(id)}&tab=blackboard`;
  return (
    <section className="aoe-worker-record" aria-label={`${agent.display_name} 战绩`}>
      <section className="insp-section" aria-label="已解出题目">
        <h3 className="caps mono subtle" style={{ margin: 0 }}>已解出 {wins.length} 道题</h3>
        {wins.length ? (
          wins.map(({ question, attempt, seconds, assisted }) => (
            <div key={question.question_id} style={{ display: "grid", gap: 2 }}>
              <div className="insp-kv">
                <Link to={link(question.question_id)}>{question.title}</Link>
                <strong>{assisted ? "增援" : formatElapsed(seconds)}</strong>
              </div>
              <small className="subtle">{attemptLabel(question, attempt)}</small>
            </div>
          ))
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
            暂无已解出题目。
          </p>
        )}
      </section>
      <section className="insp-section" aria-label="未成功解出题目">
        <h3 className="caps mono subtle" style={{ margin: 0 }}>未成功解出 {unsuccessful.length} 道题</h3>
        {unsuccessful.length ? (
          unsuccessful.map(({ question, attempt }) => (
            <div key={question.question_id} style={{ display: "grid", gap: 2 }}>
              <Link to={link(question.question_id)}>{question.title}</Link>
              <small className="subtle">
                {question.finish_reason === "platform_solved"
                  ? "平台已由外部解出"
                  : question.status === "solved"
                    ? "其他 Worker 已解出"
                    : attempt.outcome === null
                      ? "进行中"
                      : question.status === "abandoned"
                        ? "已放弃"
                        : question.status === "cancelled"
                          ? "已取消"
                          : "尝试结束，尚未解出"}{" "}
                · {attemptLabel(question, attempt)}
              </small>
            </div>
          ))
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
            暂无未成功解出的题目。
          </p>
        )}
      </section>
    </section>
  );
}

function useElapsedNow() {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function Activity({ snapshot, agent }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent }) {
  const events = snapshot.events.filter(
    (item) =>
      item.channel_agent_id === agent.agent_id ||
      item.actor.agent_id === agent.agent_id ||
      item.payload.agent_id === agent.agent_id,
  );
  const board = snapshot.blackboard.filter(
    (item) =>
      item.actor.agent_id === agent.agent_id ||
      (Array.isArray(item.body.addressed_to) && item.body.addressed_to.includes(agent.agent_id)),
  );
  return (
    <div>
      <div className="insp-section">
        <div className="caps mono subtle">执行事件 ({events.length})</div>
        {events.length ? (
          <div style={{ margin: "0 -14px" }}>
            {[...events].reverse().map((message) => (
              <EventCard key={message.id} message={message} compact />
            ))}
          </div>
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
            暂无事件
          </p>
        )}
      </div>
      <div className="insp-section">
        <div className="caps mono subtle">黑板写入 ({board.length})</div>
        {board.length ? (
          [...board].reverse().map((entry) => (
            <article key={entry.id} style={{ display: "grid", gap: 3, paddingBottom: 8, borderBottom: "1px solid var(--c-border-subtle)" }}>
              <b className="mono" style={{ fontSize: "11px" }}>
                #{entry.seq} · {BOARD_KIND_LABELS[entry.kind]}
              </b>
              <p style={{ margin: 0, fontSize: "12px" }}>{bodyText(entry)}</p>
              <small className="mono subtle">{formatTime(entry.created_at)}</small>
            </article>
          ))
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
            暂无写入
          </p>
        )}
      </div>
    </div>
  );
}

function Controls({ snapshot, agent, onChanged }: { snapshot: TGA3RuntimeSnapshot; agent: TGA3Agent; onChanged: () => void }) {
  const [models, setModels] = useState<
    Array<{ provider_id: string; provider_name: string; model_id: string; model_name: string; protocol: ProviderProtocol }>
  >([]);
  const [selected, setSelected] = useState(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void fetchAgentModelOptions(snapshot.task.scene_id as TaskMode)
      .then((value) => {
        if (!active) return;
        setModels(
          value.models.filter(
            (model) =>
              model.ready &&
              (agent.sdk === "claude_cli"
                ? model.protocol === "anthropic"
                : agent.sdk === "codex_cli"
                  ? model.protocol === "openai_responses"
                  : true),
          ),
        );
      })
      .catch((reason) => active && setNotice(errorText(reason)));
    return () => {
      active = false;
    };
  }, [snapshot.task.scene_id, agent.sdk]);

  useEffect(() => setSelected(`${agent.provider_id}::${agent.model_id}::${agent.protocol}`), [
    agent.provider_id,
    agent.model_id,
    agent.protocol,
  ]);

  async function change(value: string) {
    setSelected(value);
    const [provider, model, protocol] = value.split("::");
    if (!provider || !model || !protocol) return;
    setBusy(true);
    setNotice(null);
    try {
      await tga3RuntimeApi.setWorkerModel(snapshot.task.id, agent.agent_id, provider, model, protocol as ProviderProtocol);
      setNotice("模型已切换，将用于该 Agent 的后续周期。");
      onChanged();
    } catch (reason) {
      setNotice(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  async function control(action: "pause" | "resume") {
    setBusy(true);
    setNotice(null);
    try {
      await tga3RuntimeApi.setWorkerState(snapshot.task.id, agent.agent_id, action);
      setNotice(action === "pause" ? "暂停请求已提交，将在下一个周期检查点生效。" : "Agent 已恢复运行。");
      onChanged();
    } catch (reason) {
      setNotice(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  const worker = agent.role === "worker" && agent.runtime_location === "container";
  const paused = ["paused", "pause_requested"].includes(agent.actual_state);
  const aoeScout = Boolean(snapshot.aoe && agent.role === "scout");
  const parentTerminal = ["completed", "failed", "cancelled", "stopped"].includes(snapshot.task.state);
  const scoutWatch = snapshot.aoe?.scout_watch ?? null;
  const scoutCollecting = Boolean(snapshot.aoe?.scout_collecting || (snapshot.aoe && !snapshot.aoe.collection_complete));

  async function scoutAction(fn: () => Promise<unknown>, okText: string) {
    setBusy(true);
    setNotice(null);
    try {
      await fn();
      setNotice(okText);
      onChanged();
    } catch (reason) {
      setNotice(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="insp-section">
        <div className="caps mono subtle">热切换后续模型</div>
        <ModelPicker
          label="供应商 / 已启用模型"
          options={models.map((model) => ({ ...model, ready: true }))}
          value={(() => {
            const [provider_id, model_id, protocol] = selected.split("::");
            return { provider_id, model_id, protocol: protocol as ProviderProtocol };
          })()}
          disabled={busy || !worker}
          onChange={(model) => void change(`${model.provider_id}::${model.model_id}::${model.protocol}`)}
        />
      </div>

      <div className="insp-section">
        <div className="caps mono subtle">执行控制</div>
        {worker ? (
          <button
            type="button"
            className="ref-secondary-button"
            disabled={busy}
            onClick={() => void control(paused ? "resume" : "pause")}
          >
            {paused ? <CirclePlay size={14} /> : <CirclePause size={14} />}
            {paused ? "恢复 Worker" : "暂停 Worker"}
          </button>
        ) : aoeScout && !parentTerminal ? (
          <div style={{ display: "grid", gap: 6 }}>
            {scoutCollecting ? (
              <button
                type="button"
                className="ref-secondary-button"
                disabled={busy}
                onClick={() => void scoutAction(() => tga3RuntimeApi.stopScout(snapshot.task.id), "已停止本轮 Scout 收集。")}
              >
                <Square size={13} /> 停止 Scout 收集
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="ref-secondary-button"
                  disabled={busy}
                  onClick={() =>
                    void scoutAction(() => tga3RuntimeApi.wakeScout(snapshot.task.id), "已唤醒 Scout 开始增量收题。")
                  }
                >
                  <RefreshCw size={13} /> 唤醒 Scout 增量巡检
                </button>
                {scoutWatch?.enabled ? (
                  <button
                    type="button"
                    className="ref-secondary-button"
                    disabled={busy}
                    onClick={() => void scoutAction(() => tga3RuntimeApi.stopScout(snapshot.task.id), "已暂停自动站岗。")}
                  >
                    <CirclePause size={13} /> 停止自动站岗
                  </button>
                ) : scoutWatch?.probe_script_present ? (
                  <button
                    type="button"
                    className="ref-primary-button"
                    disabled={busy}
                    onClick={() =>
                      void scoutAction(
                        () => tga3RuntimeApi.configureScoutWatch(snapshot.task.id, "enable_sentinel"),
                        "已开启零 Token 哨兵站岗。",
                      )
                    }
                  >
                    <ShieldCheck size={13} /> 开启零 Token 哨兵
                  </button>
                ) : null}
              </>
            )}
          </div>
        ) : (
          <p className="muted" style={{ margin: 0, fontSize: "12px" }}>
            该角色由任务生命周期启动一次性容器，不提供独立暂停或运行中切换模型。
          </p>
        )}
        {notice ? (
          <p className="settings-message" role="status">
            {notice}
          </p>
        ) : null}
      </div>
    </div>
  );
}

function agentTools(agent: TGA3Agent) {
  if (agent.role === "scout" && agent.metadata?.scout_phase === "collecting")
    return ["题面与附件收集", "题目登记", "Shell", "文件系统"];
  if (agent.role === "scout")
    return agent.metadata?.scout_phase === "merging"
      ? ["原始 Intel 只读", "合并 Intel JSON 写入"]
      : agent.metadata?.scout_phase === "extracting"
        ? ["只读 logs", "原始 Intel JSON 写入"]
        : ["原始结果留存", "Kali 侦察工具", "Shell", "文件系统", "Skills"];
  if (agent.role === "reporter") return ["共享黑板只读", "Skills", "Markdown Writeup"];
  return ["共享黑板", "CTF 知识库", "Skills", "Shell", "文件系统", "网络工具"];
}
function errorText(reason: unknown) {
  return reason instanceof Error ? reason.message : "操作失败";
}
