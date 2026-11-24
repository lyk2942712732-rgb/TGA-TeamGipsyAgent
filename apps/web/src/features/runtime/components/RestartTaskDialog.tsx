import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Copy, RotateCcw, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { modelCompatible } from "../../../api/model-compatibility";
import type { ProviderProtocol } from "../../../api/tga3-config";
import type { RestartTaskPayload } from "../../../api/tga3-runtime-control";
import { fetchAgentModelOptions, type AgentModelOptions } from "../../../api/tga3-tasks";
import { ModelPicker } from "../../../components/ModelPicker";
import type { TaskMode } from "../../../modes";
import type { TGA3RuntimeSnapshot } from "../../../runtime/tga3-runtime";
import { aoeBudgets } from "../aoe-view";

export function RestartTaskDialog({
  open,
  snapshot,
  busy,
  error,
  onConfirm,
  onCloneNew,
  onCancel,
}: {
  open: boolean;
  snapshot: TGA3RuntimeSnapshot;
  busy: boolean;
  error: string | null;
  onConfirm: (payload: RestartTaskPayload) => void;
  onCloneNew: () => void;
  onCancel: () => void;
}) {
  const isAoe = Boolean(snapshot.aoe);
  const overview = snapshot.aoe;
  const initialBudgets = overview ? aoeBudgets(overview) : { first: 1800, second: 900, joint: 1200 };

  const [rerunScout, setRerunScout] = useState(false);
  const [retryAbandoned, setRetryAbandoned] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [aoeMinutes, setAoeMinutes] = useState({
    first: String(Math.max(1, Math.round(initialBudgets.first / 60))),
    second: String(Math.max(1, Math.round(initialBudgets.second / 60))),
    joint: String(Math.max(1, Math.round(initialBudgets.joint / 60))),
  });
  const [agentOptions, setAgentOptions] = useState<AgentModelOptions | null>(null);
  const [agentModels, setAgentModels] = useState<
    Record<string, { provider_id: string; model_id: string; protocol: ProviderProtocol }>
  >({});
  const [modelsEdited, setModelsEdited] = useState(false);
  const [budgetsEdited, setBudgetsEdited] = useState(false);
  const [optionsError, setOptionsError] = useState("");

  useEffect(() => {
    if (!open) return;
    const budgets = snapshot.aoe ? aoeBudgets(snapshot.aoe) : { first: 1800, second: 900, joint: 1200 };
    const questions = snapshot.aoe?.questions ?? [];
    const unfinishedCount = questions.filter((q) => !["solved", "abandoned"].includes(q.status)).length;
    const abandonedCount = questions.filter((q) => q.status === "abandoned").length;
    setRerunScout(Boolean(snapshot.aoe && (!snapshot.aoe.collection_complete || !questions.length)));
    setRetryAbandoned(Boolean(snapshot.aoe && unfinishedCount === 0 && abandonedCount > 0));
    setAdvancedOpen(false);
    setModelsEdited(false);
    setBudgetsEdited(false);
    setOptionsError("");
    setAoeMinutes({
      first: String(Math.max(1, Math.round(budgets.first / 60))),
      second: String(Math.max(1, Math.round(budgets.second / 60))),
      joint: String(Math.max(1, Math.round(budgets.joint / 60))),
    });
  }, [open, snapshot.task.id]);

  useEffect(() => {
    if (!open || !advancedOpen || agentOptions) return;
    let active = true;
    void fetchAgentModelOptions(snapshot.task.scene_id as TaskMode)
      .then((value) => {
        if (!active) return;
        setAgentOptions(value);
        const initial: Record<string, { provider_id: string; model_id: string; protocol: ProviderProtocol }> = {};
        for (const agent of value.agents) {
          const existing = snapshot.agents.find((item) => item.agent_id === agent.id);
          const candidate = existing
            ? { provider_id: existing.provider_id, model_id: existing.model_id, protocol: existing.protocol as ProviderProtocol }
            : { provider_id: agent.model.provider_id, model_id: agent.model.model_id, protocol: agent.model.protocol };
          const valid = value.models.some(
            (m) =>
              m.provider_id === candidate.provider_id &&
              m.model_id === candidate.model_id &&
              m.protocol === candidate.protocol &&
              m.ready &&
              modelCompatible(agent.runtime, m.protocol),
          );
          initial[agent.id] = valid
            ? candidate
            : { provider_id: agent.model.provider_id, model_id: agent.model.model_id, protocol: agent.model.protocol };
        }
        setAgentModels(initial);
      })
      .catch((reason: unknown) => {
        if (active) setOptionsError(reason instanceof Error ? reason.message : "无法读取模型列表");
      });
    return () => {
      active = false;
    };
  }, [open, advancedOpen, agentOptions, snapshot.task.scene_id, snapshot.agents]);

  if (!open) return null;

  // Compute human-readable execution plan preview.
  const questions = overview?.questions ?? [];
  const solvedCount = questions.filter((q) => q.status === "solved").length;
  const abandonedCount = questions.filter((q) => q.status === "abandoned").length;
  const unfinishedCount = questions.filter((q) => !["solved", "abandoned"].includes(q.status)).length;
  const scoutAgent = snapshot.agents.find((a) => a.role === "scout");
  const scoutDone = isAoe
    ? Boolean(overview?.collection_complete && questions.length > 0)
    : scoutAgent?.metadata?.scout_phase === "completed";
  const hasFinal = snapshot.blackboard.some((entry) => entry.kind === "final_candidate");
  const intelCount = snapshot.blackboard.filter((entry) => entry.kind === "intel").length;
  const findingCount = snapshot.blackboard.filter((entry) => entry.kind === "finding").length;

  let startSummary = "";
  let nothingToResume = false;
  if (isAoe) {
    const needScout = rerunScout || !scoutDone;
    const totalToRun = unfinishedCount + (retryAbandoned ? abandonedCount : 0);
    if (needScout) {
      startSummary = questions.length
        ? `将从 Scout 补收题目开始（已登记的 ${questions.length} 道题自动保留、幂等跳过），随后继续调度解题。`
        : "尚未完成题目收集，将从 Scout 收集阶段重新开始。";
    } else if (totalToRun > 0) {
      const parts = [];
      if (unfinishedCount > 0) parts.push(`继续调度 ${unfinishedCount} 道未完成题`);
      if (retryAbandoned && abandonedCount > 0) parts.push(`重新挑战 ${abandonedCount} 道已放弃题`);
      startSummary = `跳过 Scout，直接进入 Worker 解题阶段：${parts.join("，")}（保留 ${solvedCount} 道已解出题目）。`;
    } else {
      startSummary = "当前所有题目均已解出或已放弃。你可以勾选“重新挑战已放弃题目”或“重跑 Scout 补收漏题”，或选择“基于此任务新建”开启全新运行。";
      nothingToResume = true;
    }
  } else if (hasFinal && snapshot.task.state === "failed" && !rerunScout) {
    startSummary = "已存在通过验证的最终候选，将直接补跑 Reporter 生成最终报告。";
  } else if (scoutDone && !rerunScout) {
    startSummary = `跳过 Scout，保留黑板中已有的 ${intelCount} 条 Intel 与 ${findingCount} 条 Finding，直接拉起 Workers 续跑。`;
  } else {
    startSummary = "将从 Scout 侦察阶段重新开始（保留已上传附件与历史黑板记录）。";
  }

  const validBudgets =
    !isAoe ||
    !budgetsEdited ||
    Object.values(aoeMinutes).every((val) => Number.isInteger(Number(val)) && Number(val) >= 1 && Number(val) <= 1440);

  function submit() {
    if (busy || !validBudgets) return;
    const payload: RestartTaskPayload = {
      rerun_scout: rerunScout,
      ...(isAoe ? { retry_abandoned: retryAbandoned } : {}),
      ...(isAoe && budgetsEdited
        ? {
            aoe_first_attempt_minutes: Number(aoeMinutes.first),
            aoe_second_attempt_minutes: Number(aoeMinutes.second),
            aoe_joint_minutes: Number(aoeMinutes.joint),
          }
        : {}),
      ...(modelsEdited && Object.keys(agentModels).length ? { agent_models: agentModels } : {}),
    };
    onConfirm(payload);
  }

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="confirm-dialog restart-task-dialog" role="dialog" aria-modal="true" aria-labelledby="restart-dialog-title">
        <header className="restart-dialog-head">
          <div>
            <h2 id="restart-dialog-title">重新启动任务 · {snapshot.task.title}</h2>
            <p>原地续跑会保留当前任务 ID 与历史成果，从断点继续推进。</p>
          </div>
        </header>

        <div className="restart-plan-card" data-empty={nothingToResume ? "true" : undefined}>
          <strong>本次启动路径</strong>
          <p>{startSummary}</p>
        </div>

        <div className="restart-diff-grid">
          <div className="restart-diff-col is-keep">
            <strong><CheckCircle2 size={14} /> 保留继承</strong>
            <ul>
              {isAoe ? (
                <>
                  <li>已解出题目（{solvedCount} 道）、Flag 与报告</li>
                  <li>已登记题面、附件与黑板 Findings</li>
                  <li>各 Worker 工作区脚本与产物</li>
                </>
              ) : (
                <>
                  <li>任务描述与已上传附件</li>
                  <li>黑板已有 Intel（{intelCount}）与 Findings（{findingCount}）</li>
                  <li>各 Agent 工作区脚本与产物</li>
                </>
              )}
            </ul>
          </div>
          <div className="restart-diff-col is-reset">
            <strong><RefreshCw size={14} /> 自动重置</strong>
            <ul>
              <li>重新创建 Worker 容器与干净对话会话</li>
              {isAoe ? <li>动态靶场重新申请，本轮倒计时重新起算</li> : <li>清除旧错误状态，恢复调度执行</li>}
              {isAoe ? <li>上次被中断的尝试不扣减独立尝试次数</li> : <li>黑板自动写入一条重启续跑提示</li>}
            </ul>
          </div>
        </div>

        <div className="restart-options">
          <label className="restart-checkbox">
            <input
              type="checkbox"
              checked={rerunScout}
              onChange={(event) => setRerunScout(event.target.checked)}
              disabled={isAoe && !scoutDone}
            />
            <span>
              <strong>{isAoe ? "重新运行 Scout 补收题目" : "强制重跑 Scout 侦察"}</strong>
              <small>
                {isAoe
                  ? scoutDone
                    ? "适合上次部分收集有漏题的情况；已登记题目会自动幂等跳过。"
                    : "上次尚未完成收集，本次将自动从 Scout 收集继续。"
                  : "取消勾选则跳过 Scout，直接复用已有 Intel。"}
              </small>
            </span>
          </label>

          {isAoe && abandonedCount > 0 ? (
            <label className="restart-checkbox">
              <input
                type="checkbox"
                checked={retryAbandoned}
                onChange={(event) => setRetryAbandoned(event.target.checked)}
              />
              <span>
                <strong>重新挑战已放弃的题目（共 {abandonedCount} 道）</strong>
                <small>将已放弃题目放回待解题队列，保留历史黑板线索并给予新一轮完整预算。</small>
              </span>
            </label>
          ) : null}
        </div>

        <div className="restart-advanced">
          <button
            type="button"
            className="text-button restart-advanced-toggle"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((v) => !v)}
          >
            {advancedOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            高级选项：调整{isAoe ? "单题预算或 " : ""}Solver 模型（可选）
          </button>

          {advancedOpen ? (
            <div className="restart-advanced-body">
              {isAoe ? (
                <div className="restart-budget-row">
                  {([
                    ["first", "首次尝试（分钟）"],
                    ["second", "第二次尝试（分钟）"],
                    ["joint", "共同攻坚（分钟）"],
                  ] as const).map(([key, label]) => (
                    <label key={key}>
                      <span>{label}</span>
                      <input
                        type="number"
                        min={1}
                        max={1440}
                        step={1}
                        value={aoeMinutes[key]}
                        onChange={(event) => {
                          setBudgetsEdited(true);
                          setAoeMinutes((prev) => ({ ...prev, [key]: event.target.value }));
                        }}
                      />
                    </label>
                  ))}
                </div>
              ) : null}

              {optionsError ? <p className="inline-error" role="alert">{optionsError}</p> : null}
              {agentOptions ? (
                <div className="restart-models-list">
                  {agentOptions.agents.map((agent) => {
                    const compatible = agentOptions.models.filter((model) => modelCompatible(agent.runtime, model.protocol));
                    return (
                      <div className="task-agent-model-row" key={agent.id}>
                        <span><strong>{agent.display_name}</strong><small>{agent.id}</small></span>
                        <ModelPicker
                          label={`${agent.display_name} 重启模型`}
                          options={compatible}
                          value={agentModels[agent.id]}
                          onChange={(model) => {
                            setModelsEdited(true);
                            setAgentModels((prev) => ({ ...prev, [agent.id]: model }));
                          }}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="tga3-muted">正在加载可用模型…</p>
              )}
            </div>
          ) : null}
        </div>

        {error ? (
          <p className="inline-error restart-error" role="alert">
            <AlertTriangle size={14} /> {error}
          </p>
        ) : null}

        <footer className="restart-dialog-footer">
          <button type="button" className="text-button restart-clone-link" disabled={busy} onClick={onCloneNew}>
            <Copy size={14} /> 想清空历史重来？改为基于此任务新建
          </button>
          <div className="restart-dialog-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={onCancel}>取消</button>
            <button
              type="button"
              className="ref-primary-button"
              disabled={busy || !validBudgets || nothingToResume}
              onClick={submit}
            >
              <RotateCcw size={14} /> {busy ? "正在重启…" : "确认原地重启"}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}
