const configuredApiBase = import.meta.env.VITE_TGA_API_BASE as string | undefined;

/**
 * Resolve the API origin once for every browser bundle.
 *
 * A public deployment normally serves the SPA and `/api` from the same
 * origin.  Using `window.location.origin` as the fallback keeps that setup
 * working for an IP address, a domain name, HTTPS, and a reverse proxy.  A
 * separate API service can still be selected explicitly at build time with
 * VITE_TGA_API_BASE.
 */
export function resolveApiBase(configuredBase?: string, pageOrigin?: string): string {
  const configured = configuredBase?.trim();
  if (configured) return configured.replace(/\/$/, "");

  const origin = pageOrigin?.trim();
  if (origin && origin !== "null") return origin.replace(/\/$/, "");

  // This branch is for non-browser callers such as SSR/tests.  The normal
  // browser fallback above deliberately follows the page's current origin.
  return "http://127.0.0.1:5173";
}

const pageOrigin = typeof window === "undefined" ? undefined : window.location.origin;
export const apiBase = resolveApiBase(configuredApiBase, pageOrigin);

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

type ValidationIssue = { loc?: Array<string | number>; msg?: string };
type ApiErrorDetail = string | { code?: string; message?: string; reason?: string } | ValidationIssue[];

export function formatApiErrorDetail(detail: ApiErrorDetail | undefined, status: number): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const issues = detail.map((issue) => {
      const field = issue.loc?.filter((part) => part !== "body").join(".");
      return [field, issue.msg].filter(Boolean).join(": ");
    }).filter(Boolean);
    if (issues.length) return issues.slice(0, 4).join("；");
  }
  if (detail && !Array.isArray(detail) && typeof detail === "object") {
    const heading = [detail.code, detail.message].filter(Boolean).join(": ");
    const reason = detail.reason?.trim();
    if (heading && reason) return `${heading} — ${reason.slice(0, 800)}`;
    if (heading) return heading;
    if (reason) return reason.slice(0, 800);
  }
  return `请求失败：${status}`;
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: ApiErrorDetail; message?: string } | null;
    const detail = body?.detail ?? body?.message;
    const message = detail === "model_not_configured"
      ? "尚未配置模型，请先到模型设置页完成配置和工具协议验证。"
      : formatApiErrorDetail(detail, response.status);
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}
