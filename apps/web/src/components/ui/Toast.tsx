import { Info } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

/**
 * Transient bottom-centre notices.
 *
 * The reference designs specify a full set of write controls — 新建 Solver,
 * 上传, 导出, 复制模板, 编辑新版本 — that no backend endpoint implements.  Those
 * controls stay clickable and answer with `notifyUnavailable`, so the layout
 * matches the design without pretending the action succeeded.  Controls that
 * DO have an endpoint (Skill 导入/编辑/删除, MCP 增删启停, Models 保存/验证)
 * must never route through here.
 */

type Toast = { id: number; message: string };
type ToastApi = {
  notify: (message: string) => void;
  /** "<名称>：该功能尚未开放" — the single wording for unbuilt write actions. */
  notifyUnavailable: (feature: string) => void;
};

const ToastContext = createContext<ToastApi | null>(null);
const LIFETIME_MS = 2600;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const notify = useCallback((message: string) => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message }]);
    setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), LIFETIME_MS);
  }, []);

  const api = useMemo<ToastApi>(() => ({
    notify,
    notifyUnavailable: (feature) => notify(`${feature}：该功能尚未开放`),
  }), [notify]);

  return <ToastContext.Provider value={api}>
    {children}
    <div className="toast-stack" role="status" aria-live="polite">
      {toasts.map((toast) => <div className="toast" key={toast.id}>
        <Info size={15} aria-hidden="true" />
        <span>{toast.message}</span>
      </div>)}
    </div>
  </ToastContext.Provider>;
}

/**
 * Safe outside a provider: tests render pages in isolation and must not have to
 * wrap every one of them just to click a decorative button.
 */
export function useToast(): ToastApi {
  const value = useContext(ToastContext);
  return value ?? FALLBACK;
}

const FALLBACK: ToastApi = { notify: () => {}, notifyUnavailable: () => {} };
