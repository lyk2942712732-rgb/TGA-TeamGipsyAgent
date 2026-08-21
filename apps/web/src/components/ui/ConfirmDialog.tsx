import type { ReactNode } from "react";

export function ConfirmDialog({ open, title, description, confirmLabel = "确认", cancelLabel = "取消", danger = false, busy = false, details, onConfirm, onCancel }: { open: boolean; title: string; description: string; confirmLabel?: string; cancelLabel?: string; danger?: boolean; busy?: boolean; details?: ReactNode; onConfirm: () => void; onCancel: () => void }) {
  if (!open) return null;
  return <div className="dialog-backdrop" role="presentation"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-dialog-title">
    <h2 id="confirm-dialog-title">{title}</h2><p>{description}</p>{details}
    <div><button className="secondary-button" disabled={busy} onClick={onCancel}>{cancelLabel}</button><button className={danger ? "danger-button" : undefined} disabled={busy} onClick={onConfirm}>{busy ? "处理中..." : confirmLabel}</button></div>
  </section></div>;
}
