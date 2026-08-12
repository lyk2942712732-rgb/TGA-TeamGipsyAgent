import { Info } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

/** Transient bottom-centre notices for completed or failed real actions. */

type Toast = { id: number; message: string };
type ToastApi = {
  notify: (message: string) => void;
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

  const api = useMemo<ToastApi>(() => ({ notify }), [notify]);

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

const FALLBACK: ToastApi = { notify: () => {} };
