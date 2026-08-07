import { Bot, ClipboardList, FileText, Sparkles, Target, Users, Wrench } from "lucide-react";

const STEPS = [
  { title: "任务目标", accessible: "任务目标" },
  { title: "输入与资源", accessible: "输入与资源 / 任务提示与材料" },
  { title: "授权与执行策略", accessible: "授权与执行策略 / 执行边界" },
  { title: "团队和模型", accessible: "团队和模型" },
  { title: "启动前检查", accessible: "启动前检查 / 创建摘要" },
];

/** The four tiles inside the reference's "auto match" illustration. */
const MATCH_CHIPS = [
  { icon: Users, label: "团队" },
  { icon: Bot, label: "Solver" },
  { icon: Sparkles, label: "Skills" },
  { icon: Wrench, label: "工具" },
];

const TIPS = [
  { icon: Target, tone: "tone-info", title: "明确目标", body: "清晰的目标有助于选择合适的方法与资源，显著提升任务成功率。" },
  { icon: FileText, tone: "tone-info", title: "提供充分背景", body: "更多的背景信息能帮助团队更快理解任务范围与关键点。" },
  { icon: ClipboardList, tone: "tone-success", title: "设定约束与标准", body: "约束确保执行的安全性，成功标准用于评估任务是否完成。" },
];

export function NewTaskHeader() {
  return <header className="ref-page-head">
    <div>
      <h1>创建任务 · 五步向导</h1>
      <p>定义任务目标、输入与授权边界；匹配场景的 Skills 和已启用能力将在创建时冻结。</p>
    </div>
  </header>;
}

export function NewTaskProgress({ step, completedSteps, onStep }: { step: number; completedSteps: ReadonlySet<number>; onStep: (step: number) => void }) {
  return <nav className="wizard-steps" aria-label="创建步骤">{STEPS.map((item, index) => {
    const number = index + 1;
    const complete = completedSteps.has(number);
    return <button
      key={item.title}
      type="button"
      aria-label={item.accessible}
      aria-current={step === number ? "step" : undefined}
      className={`${step === number ? "active" : ""} ${complete ? "complete" : ""}`}
      data-complete={complete ? "true" : "false"}
      onClick={() => onStep(number)}
    >
      <b>{complete ? "✓" : number}</b>
      <span>{item.title}</span>
    </button>;
  })}</nav>;
}

/** The reference's right-hand guidance rail. Static copy, no data source. */
export function NewTaskGuide({ modeLabel, modeDescription }: { modeLabel: string; modeDescription: string }) {
  return <aside className="wizard-guide" aria-label="创建说明">
    <section className="ref-card">
      <header className="ref-card-head"><h2>说明</h2></header>
      <ul className="wizard-tips">
        {TIPS.map((tip) => <li key={tip.title}>
          <span className={`row-icon ${tip.tone}`} aria-hidden="true"><tip.icon size={15} /></span>
          <div>
            <strong>{tip.title}</strong>
            <p>{tip.body}</p>
          </div>
        </li>)}
      </ul>
    </section>

    <section className="ref-card">
      <header className="ref-card-head"><h2>模式会自动匹配团队与方法</h2></header>
      {/* The rail is narrow, so the mode's full description stays in the title
          attribute rather than pushing the card past the card below it. */}
      <p className="wizard-guide-copy" title={modeDescription}>
        当前场景「{modeLabel}」。系统会据此推荐最合适的团队、Solver、Skills
        与工具链，你也可以在后续步骤中自定义。
      </p>
      <div className="wizard-match-preview" aria-hidden="true">
        <i />
        <div>
          {MATCH_CHIPS.map((chip) => <span key={chip.label}>
            <chip.icon size={11} /><em>{chip.label}</em>
          </span>)}
        </div>
        <b>✓</b>
      </div>
    </section>
  </aside>;
}
