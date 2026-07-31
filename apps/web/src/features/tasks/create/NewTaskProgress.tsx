import { PageHeader } from "../../../components/ui/PageHeader";

const STEPS = [
  { title: "场景与目标", accessible: "选择场景与任务目标", legacy: "" },
  { title: "任务配置", accessible: "任务配置", legacy: "" },
  { title: "输入与资源", accessible: "任务提示与材料 / 输入与资源", legacy: "任务提示与材料" },
  { title: "执行策略", accessible: "执行边界 / 执行策略", legacy: "执行边界" },
  { title: "启动前检查", accessible: "创建摘要 / 启动前检查", legacy: "创建摘要" },
];

export function NewTaskHeader() {
  return <PageHeader
    eyebrow="TASKS / CREATE"
    title="新建任务"
    description="定义任务目标、输入与授权边界；匹配场景的 Skills 和已启用能力将在创建时冻结。"
    breadcrumbs={[{ label: "TGA", href: "/" }, { label: "任务", href: "/tasks" }, { label: "新建任务" }]}
  />;
}

export function NewTaskProgress({ step, onStep }: { step: number; onStep: (step: number) => void }) {
  return <nav className="wizard-steps" aria-label="创建步骤">{STEPS.map((item, index) => {
    const number = index + 1;
    return <button key={item.title} type="button" aria-label={item.accessible} aria-current={step === number ? "step" : undefined} className={`${step === number ? "active" : ""} ${step > number ? "complete" : ""}`} onClick={() => onStep(number)}>
      <b>{step > number ? "✓" : number}</b><span>{item.title}</span>{item.legacy ? <span className="sr-only">{item.legacy}</span> : null}
    </button>;
  })}</nav>;
}
