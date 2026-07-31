import { AlertTriangle } from "lucide-react";

export function ErrorState({ title = "加载失败", description, actionLabel, onAction }: { title?: string; description: string; actionLabel?: string; onAction?: () => void }) {
  return <section className="error-state" role="alert">
    <span><AlertTriangle size={20} aria-hidden="true" /></span>
    <div><h2>{title}</h2><p>{description}</p></div>
    {actionLabel && onAction ? <button onClick={onAction}>{actionLabel}</button> : null}
  </section>;
}
