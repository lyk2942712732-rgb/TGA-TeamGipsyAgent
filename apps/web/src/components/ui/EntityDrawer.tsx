import { X } from "lucide-react";
import type { ReactNode } from "react";

export function EntityDrawer({ open, title, description, children, footer, onClose }: { open: boolean; title: string; description?: string; children: ReactNode; footer?: ReactNode; onClose: () => void }) {
  if (!open) return null;
  return <div className="entity-drawer-layer">
    <button className="entity-drawer-backdrop" aria-label="关闭详情" onClick={onClose} />
    <aside className="entity-drawer" role="dialog" aria-modal="true" aria-labelledby="entity-drawer-title">
      <header><div><h2 id="entity-drawer-title">{title}</h2>{description ? <p>{description}</p> : null}</div><button aria-label="关闭详情" onClick={onClose}><X size={17} /></button></header>
      <div className="entity-drawer-content">{children}</div>
      {footer ? <footer>{footer}</footer> : null}
    </aside>
  </div>;
}
