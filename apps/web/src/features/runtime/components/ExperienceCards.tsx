import { useEffect, useState } from "react";
import { apiBase, requestJson } from "../../../api/client";

export type ExperienceCard = {
  card_id: string; title: string;
  applies: { signals: string[]; requires: string[] };
  steps: string[]; verify: string[]; limits: string[]; resources: string[];
  sources: Array<{ task_id: string; question_id: string; action_ids: string[]; validation: string }>;
};
type CardResponse = { cards: ExperienceCard[]; state: Record<string, { status: string; error?: string }> };

export function ExperienceCards({ taskId }: { taskId: string }) {
  const [result, setResult] = useState<CardResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setResult(null); setError("");
    const load = () => requestJson<CardResponse>(`/api/v3/tasks/${encodeURIComponent(taskId)}/experience`)
      .then(value => { if (active) { setResult(value); setError(""); } })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    void load(); const timer = window.setInterval(load, 10000);
    return () => { active = false; window.clearInterval(timer); };
  }, [taskId]);
  if (error) return <p role="alert">经验卡读取失败：{error}</p>;
  if (!result) return <p role="status">正在读取本任务经验卡…</p>;
  return <section className="experience-cards" aria-label="本任务经验卡">
    <p>本任务成功题目中解决具体问题的经验。遇到相应问题时，核对条件并在当前题目验证。</p>
    {!result.cards.length ? <p>尚无经验卡。成功解题后，Reporter 会在整理报告时提炼已解决的具体问题。</p> : null}
    {Object.entries(result.state).filter(([, item]) => item.status === "error").map(([id, item]) =>
      <p role="status" key={id}>题目 {id} 的卡片尚未导入：{item.error}</p>)}
    {result.cards.map(card => <details key={card.card_id} className="experience-card">
      <summary><strong>{card.title}</strong><small>{card.card_id} · {card.applies.signals.join(" / ")}</small></summary>
      <div className="experience-card-body">
        {([
          ["必要条件", card.applies.requires], ["方法步骤", card.steps],
          ["验证办法", card.verify], ["适用边界", card.limits],
        ] as const).map(([title, items]) => items.length ? <div key={title}><h4>{title}</h4><ol>{items.map((text, index) => <li key={index}>{text}</li>)}</ol></div> : null)}
        {card.resources.length ? <div><h4>可复用资源</h4><ul>{card.resources.map(path => <li key={path}>
          <a href={`${apiBase}/api/v3/tasks/${encodeURIComponent(taskId)}/experience/${encodeURIComponent(card.card_id)}/resources/${path.split("/").map(encodeURIComponent).join("/")}`} download>{path.split("/").pop()}</a>
        </li>)}</ul></div> : null}
        <div><h4>来源</h4>{card.sources.map((source, index) => <p key={index}>
          <a href={`/tasks/${source.task_id}/runtime?tab=blackboard&question=${source.question_id}`}>查看来源题目</a>
          {" · "}{source.validation === "reproduced" ? "记录支持复现" : "执行记录支持"}
          <small>{source.action_ids.join("、")}</small>
        </p>)}</div>
        <details><summary>查看完整 JSON</summary><pre>{JSON.stringify(card, null, 2)}</pre></details>
      </div>
    </details>)}
  </section>;
}
