import { ModelPicker, PROTOCOL_NAMES } from "../components/ModelPicker";
import { Bot, Box, CheckCircle2, Copy, Cpu, Database, RefreshCw, Save, Sparkles, Wrench, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  ALL_RUNTIMES,
  RUNTIME_LABELS,
  RUNTIME_PROTOCOL_SUMMARY,
  preferredProtocolForRuntime,
} from "../api/model-compatibility";
import {
  tga3ConfigApi,
  type AgentConfig,
  type AgentDefinition,
  type AgentRuntime,
  type AgentsConfig,
  type ImageHealthStatus,
  type ModelsConfig,
  type ProviderProtocol,
  type RuntimeDefinition,
} from "../api/tga3-config";
import { enabledModelOptions } from "../api/model-options";
import { ErrorState } from "../components/ui/ErrorState";
import { LoadingSkeleton } from "../components/ui/LoadingSkeleton";

const AGENT_ORDER = ["scout", "worker-openai", "worker-claude", "worker-opencode", "worker-qwen", "reporter"];
type ConfigTab = "model" | "prompt" | "tools";

type AssemblyPreset = {
  id: string;
  label: string;
  assign: (agentId: string, role: AgentConfig["role"]) => AgentRuntime;
};

const ASSEMBLY_PRESETS: readonly AssemblyPreset[] = [
  {
    id: "mixed",
    label: "四家混编",
    assign: (id) =>
      id === "worker-claude"
        ? "claude_cli"
        : id === "worker-opencode"
          ? "opencode_cli"
          : id === "worker-qwen"
            ? "qwen_cli"
            : "codex_cli",
  },
  { id: "all-codex", label: "全员 Codex", assign: () => "codex_cli" },
  { id: "all-claude", label: "全员 Claude", assign: () => "claude_cli" },
];

export function SolversPage() {
  const [agents, setAgents] = useState<AgentsConfig | null>(null);
  const [models, setModels] = useState<ModelsConfig | null>(null);
  const [definitions, setDefinitions] = useState<AgentDefinition[]>([]);
  const [runtimes, setRuntimes] = useState<RuntimeDefinition[]>([]);
  const [selectedId, setSelectedId] = useState("worker-openai");
  const [tab, setTab] = useState<ConfigTab>("model");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [scoutSeconds, setScoutSeconds] = useState(300);
  const [scoutTimeDirty, setScoutTimeDirty] = useState(false);
  const [pendingProtocols, setPendingProtocols] = useState(new Map<string, ProviderProtocol>());

  async function load() {
    setError("");
    try {
      const [a, m, d, r] = await Promise.all([
        tga3ConfigApi.agents(),
        tga3ConfigApi.models(),
        tga3ConfigApi.agentDefinitions(),
        tga3ConfigApi.runtimes ? tga3ConfigApi.runtimes().catch(() => []) : Promise.resolve([]),
      ]);
      setAgents(a);
      setModels(m);
      setDefinitions(d);
      setRuntimes(r);
      setScoutSeconds(d.find((item) => item.id === "scout")?.scout_timeout_seconds ?? 300);
      setScoutTimeDirty(false);
      setSelectedId((current) => (a.agents[current] ? current : AGENT_ORDER.find((id) => a.agents[id]) ?? ""));
      setPendingProtocols(new Map());
      setDirty(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Solver 配置");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const selected = agents?.agents[selectedId];
  const definition = definitions.find((item) => item.id === selectedId);
  const runtimeMap = useMemo(() => new Map(runtimes.map((item) => [item.id, item])), [runtimes]);
  const options = useMemo(() => enabledModelOptions(models), [models]);
  const availableProtocols = useMemo(() => [...new Set(options.map((item) => item.protocol))], [options]);
  const pendingProtocol = pendingProtocols.get(selectedId);
  const protocol = pendingProtocol ?? selected?.protocol;
  const modelChoices = options
    .filter((item) => item.protocol === protocol)
    .map(({ provider, model, protocol: proto }) => ({
      provider_id: provider.id,
      provider_name: provider.name,
      model_id: model.id,
      model_name: model.name,
      protocol: proto,
      ready: true,
    }));
  const protocols = selected ? solverProtocols(selected, options) : [];
  const modelSelectionRequired = pendingProtocols.size > 0;

  const activeRuntimeDef = selected ? runtimeMap.get(selected.runtime) : undefined;
  const activeTools =
    (selected && activeRuntimeDef?.tools_by_role?.[selected.role]) ?? definition?.tools ?? [];
  const activeImage = activeRuntimeDef?.image ?? definition?.image ?? null;
  const activeImageHealth: ImageHealthStatus = activeRuntimeDef?.image_health ?? definition?.image_health ?? null;

  function patch(value: Partial<AgentConfig>) {
    if (!agents || !selected) return;
    setAgents({ ...agents, agents: { ...agents.agents, [selectedId]: { ...selected, ...value } } });
    setDirty(true);
    setMessage("");
  }

  function setPendingProtocol(nextProtocol?: ProviderProtocol, targetId = selectedId) {
    setPendingProtocols((current) => {
      const next = new Map(current);
      if (nextProtocol) next.set(targetId, nextProtocol);
      else next.delete(targetId);
      return next;
    });
    setMessage("");
  }

  function changeProtocol(nextProtocol: ProviderProtocol) {
    if (!selected) return;
    if (nextProtocol === selected.protocol) {
      setPendingProtocol();
    } else if (
      options.some(
        (item) =>
          item.protocol === nextProtocol &&
          item.provider.id === selected.provider_id &&
          item.model.id === selected.model_id,
      )
    ) {
      setPendingProtocol();
      patch({ protocol: nextProtocol });
    } else {
      setPendingProtocol(nextProtocol);
    }
  }

  function changeRuntime(nextRuntime: AgentRuntime) {
    if (!selected || nextRuntime === selected.runtime) return;
    const nextProtocol = preferredProtocolForRuntime(nextRuntime, selected.protocol, availableProtocols);
    const modelStillValid = options.some(
      (item) =>
        item.protocol === nextProtocol &&
        item.provider.id === selected.provider_id &&
        item.model.id === selected.model_id,
    );
    patch({ runtime: nextRuntime, protocol: nextProtocol });
    if (modelStillValid) {
      setPendingProtocol(undefined, selectedId);
    } else {
      setPendingProtocol(nextProtocol, selectedId);
    }
  }

  function applyPreset(preset: AssemblyPreset) {
    if (!agents) return;
    const updatedAgents: Record<string, AgentConfig> = {};
    const nextPending = new Map<string, ProviderProtocol>();
    for (const [id, agent] of Object.entries(agents.agents)) {
      const nextRuntime = preset.assign(id, agent.role);
      const nextProtocol = preferredProtocolForRuntime(nextRuntime, agent.protocol, availableProtocols);
      const currentValid = options.some(
        (item) =>
          item.protocol === nextProtocol &&
          item.provider.id === agent.provider_id &&
          item.model.id === agent.model_id,
      );
      if (currentValid) {
        updatedAgents[id] = { ...agent, runtime: nextRuntime, protocol: nextProtocol };
      } else {
        const fallback = options.find((item) => item.protocol === nextProtocol);
        if (fallback) {
          updatedAgents[id] = {
            ...agent,
            runtime: nextRuntime,
            protocol: nextProtocol,
            provider_id: fallback.provider.id,
            model_id: fallback.model.id,
          };
        } else {
          updatedAgents[id] = { ...agent, runtime: nextRuntime, protocol: nextProtocol };
          nextPending.set(id, nextProtocol);
        }
      }
    }
    setAgents({ ...agents, agents: updatedAgents });
    setPendingProtocols(nextPending);
    setDirty(true);
    setMessage(
      nextPending.size
        ? `已套用「${preset.label}」编队模板，部分槽位当前协议无可用模型，请先补选后再保存。`
        : `已套用「${preset.label}」编队模板，核对后点击保存生效。`,
    );
  }

  function syncPromptToWorkers() {
    if (!agents || !selected || selected.role !== "worker") return;
    const updatedAgents: Record<string, AgentConfig> = {};
    for (const [id, agent] of Object.entries(agents.agents)) {
      updatedAgents[id] =
        agent.role === "worker" ? { ...agent, system_prompt: selected.system_prompt } : agent;
    }
    setAgents({ ...agents, agents: updatedAgents });
    setDirty(true);
    setMessage("已将当前提示词同步到全部 Worker 槽位，记得保存配置。");
  }

  async function save() {
    if (!agents || modelSelectionRequired) return;
    if (scoutTimeDirty && (!Number.isInteger(scoutSeconds) || scoutSeconds < 60 || scoutSeconds > 600)) {
      setError("Scout 第一轮时长应为 60–600 秒的整数。");
      return;
    }
    setBusy(true);
    setMessage("");
    setError("");
    try {
      if (scoutTimeDirty) {
        await tga3ConfigApi.saveScoutSettings(scoutSeconds);
        setScoutTimeDirty(false);
      }
      const saved = await tga3ConfigApi.saveAgents(agents);
      setAgents(saved);
      const refreshedDefs = await tga3ConfigApi.agentDefinitions().catch(() => definitions);
      setDefinitions(refreshedDefs);
      setDirty(false);
      setMessage("Solver 配置已保存。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ref-page solvers-page">
      <header className="ref-page-head">
        <div>
          <h1>Solver 配置</h1>
          <p>每个角色槽位可自由装配任意 CLI 引擎（Codex / Claude / OpenCode / Qwen）与兼容模型。</p>
        </div>
        <div className="solver-head-actions">
          <button
            className="ref-secondary-button"
            disabled={busy}
            onClick={() => {
              if (!(dirty || modelSelectionRequired) || window.confirm("刷新将放弃尚未保存的配置，继续吗？")) {
                void load();
              }
            }}
          >
            <RefreshCw size={15} />
            刷新状态
          </button>
          <button
            className="ref-primary-button"
            disabled={!agents || busy || modelSelectionRequired}
            onClick={() => void save()}
          >
            <Save size={15} />
            {busy ? "保存中…" : "保存"}
          </button>
        </div>
      </header>
      {error ? <ErrorState description={error} actionLabel="重试" onAction={() => void load()} /> : null}
      {message ? (
        <p className="settings-message" role="status">
          {message}
        </p>
      ) : null}
      {!agents && !error ? <LoadingSkeleton label="正在读取 Solver" rows={6} /> : null}
      {agents ? (
        <div className="workbench-solver-layout">
          <section className="workbench-team" aria-label="Solver 团队图">
            <div className="workbench-team-bar">
              <span>
                <Sparkles size={13} />
                编队装配预设
              </span>
              <div className="workbench-preset-buttons">
                {ASSEMBLY_PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    className="ref-secondary-button workbench-preset-btn"
                    disabled={busy}
                    onClick={() => applyPreset(preset)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="workbench-team-nodes">
              {AGENT_ORDER.filter((id) => agents.agents[id]).map((id) => {
                const agent = agents.agents[id];
                const provider = models?.providers.find((item) => item.id === agent.provider_id);
                const rtHealth = runtimeMap.get(agent.runtime)?.image_health;
                return (
                  <button
                    key={id}
                    className="workbench-team-node"
                    aria-label={`配置 ${agent.display_name}`}
                    aria-pressed={selectedId === id}
                    onClick={() => setSelectedId(id)}
                  >
                    <span>
                      {agent.role === "scout" ? "侦察阶段" : agent.role === "reporter" ? "报告阶段" : "并行执行"}
                    </span>
                    <strong>
                      <Bot size={14} /> {agent.display_name}
                    </strong>
                    <small className="solver-node-runtime">
                      <span className={`cli-badge is-${agent.runtime}`}>
                        <Cpu size={11} />
                        {sdkName(agent.runtime)}
                      </span>
                      {rtHealth ? (
                        <em className={`runtime-dot ${rtHealth.status}`} title={rtHealth.detail} />
                      ) : null}
                    </small>
                    <small>
                      {pendingProtocols.has(id) || !agent.model_id
                        ? "待配置"
                        : `${provider?.name ?? agent.provider_id} / ${agent.model_id}`}
                    </small>
                  </button>
                );
              })}
            </div>
            <div className="workbench-team-bus">
              <Database size={14} />
              共享黑板：Intel → Findings → 最终候选
            </div>
          </section>

          {selected ? (
            <section className="ref-card workbench-solver-panel" aria-label={`${selected.display_name} Solver 配置`}>
              <header>
                <div>
                  <h2>{selected.display_name}</h2>
                  <p>
                    {roleName(selected.role)} / {sdkName(selected.runtime)}
                  </p>
                </div>
                {activeImageHealth ? <HealthDot status={activeImageHealth.status} /> : null}
              </header>
              <div className="workbench-tabs" role="tablist" aria-label="Solver 配置内容">
                {(
                  [
                    ["model", "运行与模型"],
                    ["prompt", "系统提示词"],
                    ["tools", "工具与镜像"],
                  ] as const
                ).map(([value, text]) => (
                  <button
                    key={value}
                    id={`solver-tab-${value}`}
                    role="tab"
                    aria-selected={tab === value}
                    aria-controls={`solver-panel-${value}`}
                    onClick={() => setTab(value)}
                  >
                    {text}
                  </button>
                ))}
              </div>
              <div
                className="workbench-solver-content"
                role="tabpanel"
                id={`solver-panel-${tab}`}
                aria-labelledby={`solver-tab-${tab}`}
              >
                {tab === "model" ? (
                  <>
                    <div className="solver-cli-section">
                      <span className="solver-section-label">1. 装配执行引擎（CLI）</span>
                      <div className="solver-cli-grid" role="group" aria-label="装配 CLI 引擎">
                        {ALL_RUNTIMES.map((rt) => {
                          const rtDef = runtimeMap.get(rt);
                          const isSelected = selected.runtime === rt;
                          return (
                            <button
                              key={rt}
                              type="button"
                              className={`solver-cli-card${isSelected ? " is-selected" : ""}`}
                              aria-pressed={isSelected}
                              disabled={busy}
                              onClick={() => changeRuntime(rt)}
                            >
                              <span className="solver-cli-card-head">
                                <strong>{RUNTIME_LABELS[rt]}</strong>
                                {rtDef?.image_health ? (
                                  <em
                                    className={`runtime-dot ${rtDef.image_health.status}`}
                                    title={rtDef.image_health.detail}
                                  />
                                ) : null}
                              </span>
                              <small>{RUNTIME_PROTOCOL_SUMMARY[rt]}</small>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div>
                      2. 供应商 / 已启用模型
                      <ModelPicker
                        label="供应商 / 已启用模型"
                        value={pendingProtocol ? undefined : selected}
                        options={modelChoices}
                        showProtocol={false}
                        disabled={busy}
                        onChange={(value) => {
                          patch(value);
                          setPendingProtocol();
                        }}
                      />
                      <small className="field-help">
                        {modelChoices.length
                          ? "仅显示当前协议端点下已启用且可用的模型。"
                          : "当前协议端点没有已启用且可用的模型，请先在模型管理页启用，或切换协议/CLI。"}
                      </small>
                    </div>

                    <p className="workbench-execution-note">
                      {selected.role === "scout"
                        ? "三轮复用同一容器和 CLI 会话。第一轮执行并汇总 action 日志；第二轮覆盖全部 action，保存原始 Intel JSON；第三轮统一合并，另存合并 Intel JSON，系统仅将合并版本导入黑板。第二、三轮不设总时间预算，完整覆盖后结束。"
                        : selected.role === "reporter"
                          ? "最终候选确定后，读取最终黑板与证据并生成报告。"
                          : "持续推进 · 不限制轮数。异常恢复继续使用原 CLI 会话。"}
                    </p>

                    {selected.role === "scout" ? (
                      <label>
                        第一轮侦察时长（秒）
                        <input
                          aria-label="第一轮侦察时长（秒）"
                          type="number"
                          min={60}
                          max={600}
                          step={1}
                          value={scoutSeconds}
                          onChange={(event) => {
                            setScoutSeconds(Number(event.target.value));
                            setScoutTimeDirty(true);
                            setDirty(true);
                            setMessage("");
                          }}
                        />
                        <small className="field-help">
                          默认 300 秒，范围 60–600 秒，仅用于单题场景。AOE Scout 只收集题面与附件，不执行后续侦察轮次，不使用此预算。保存后用于新任务。
                        </small>
                      </label>
                    ) : null}

                    {selected.role === "worker" ? (
                      <label>
                        黑板同步间隔（秒）
                        <input
                          aria-label="黑板同步间隔（秒）"
                          type="number"
                          min={1}
                          max={3600}
                          value={selected.blackboard_interval_seconds ?? 60}
                          onChange={(event) =>
                            patch({ blackboard_interval_seconds: Number(event.target.value) })
                          }
                        />
                        <small className="field-help">
                          有更新且达到间隔时，在下一次工具调用机会同步；提交最终候选前额外检查。
                        </small>
                      </label>
                    ) : null}

                    <div className="workbench-definition">
                      <span>
                        角色<strong>{roleName(selected.role)}</strong>
                      </span>
                      <span>
                        执行后端<strong>{sdkName(selected.runtime)}</strong>
                      </span>
                      <label className="workbench-protocol">
                        协议
                        <select
                          aria-label="协议"
                          value={protocol}
                          disabled={busy || protocols.length < 2}
                          onChange={(event) => changeProtocol(event.target.value as ProviderProtocol)}
                        >
                          {protocols.map((proto) => (
                            <option key={proto} value={proto}>
                              {PROTOCOL_NAMES[proto]}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </>
                ) : tab === "prompt" ? (
                  <>
                    <div className="solver-prompt-head">
                      <h3>System Prompt</h3>
                      {selected.role === "worker" ? (
                        <button
                          type="button"
                          className="ref-secondary-button"
                          disabled={busy}
                          onClick={syncPromptToWorkers}
                        >
                          <Copy size={14} />
                          同步提示词到全部 Worker
                        </button>
                      ) : null}
                    </div>
                    <textarea
                      className="solver-prompt-editor"
                      aria-label={`${selected.display_name} System Prompt`}
                      rows={15}
                      value={selected.system_prompt}
                      onChange={(event) => patch({ system_prompt: event.target.value })}
                    />
                    <p className="field-help">用于后续启动的 Agent。</p>
                  </>
                ) : (
                  <>
                    <h3>
                      任务工具（{sdkName(selected.runtime)} · {roleName(selected.role)}）
                    </h3>
                    <div className="solver-tool-grid">
                      {activeTools.map((tool) => (
                        <span key={tool}>
                          <Wrench size={13} />
                          {tool}
                        </span>
                      ))}
                    </div>
                    {activeImage ? (
                      <ImageHealth image={activeImage} health={activeImageHealth} />
                    ) : (
                      <p className="field-help">执行镜像尚未提供。</p>
                    )}
                  </>
                )}
              </div>
              <footer>
                <span role="status">
                  {modelSelectionRequired
                    ? "请为切换了引擎或协议的 Solver 选择兼容模型"
                    : dirty
                      ? "有未保存的更改"
                      : "配置已保存"}
                </span>
                <button
                  className="ref-primary-button"
                  disabled={busy || modelSelectionRequired}
                  onClick={() => void save()}
                >
                  <Save size={15} />
                  {busy ? "保存中…" : "保存配置"}
                </button>
              </footer>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function solverProtocols(
  agent: AgentConfig,
  options: ReturnType<typeof enabledModelOptions>,
): ProviderProtocol[] {
  if (agent.runtime === "codex_cli") return ["openai_responses"];
  if (agent.runtime === "claude_cli") return ["anthropic"];
  return [...new Set([agent.protocol, ...options.map((item) => item.protocol)])];
}

function HealthDot({ status }: { status: string }) {
  return (
    <span className={`image-health-dot ${status}`}>
      {status === "healthy" ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
      {status === "healthy" ? "镜像可用" : status === "missing" ? "镜像缺失" : "镜像不可用"}
    </span>
  );
}

function ImageHealth({ image, health }: { image: string; health: ImageHealthStatus }) {
  return (
    <section className="worker-image-card">
      <header>
        <div>
          <Box size={18} />
          <span>
            <small>执行镜像</small>
            <strong>{image}</strong>
          </span>
        </div>
        {health ? <HealthDot status={health.status} /> : null}
      </header>
      <dl>
        <div>
          <dt>状态</dt>
          <dd>{health?.detail ?? "未检查"}</dd>
        </div>
        {health?.image_id ? (
          <div>
            <dt>镜像 ID</dt>
            <dd>{health.image_id}</dd>
          </div>
        ) : null}
        {health?.size_bytes ? (
          <div>
            <dt>大小</dt>
            <dd>{(health.size_bytes / 1024 ** 3).toFixed(2)} GB</dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}

function roleName(value: string) {
  return ({ scout: "侦察", worker: "执行", reporter: "报告" } as Record<string, string>)[value] ?? value;
}

function sdkName(value: string) {
  return (RUNTIME_LABELS as Record<string, string>)[value] ?? value;
}
