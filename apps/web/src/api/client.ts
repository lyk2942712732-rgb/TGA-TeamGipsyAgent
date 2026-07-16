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

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string; message?: string } | null;
    throw new ApiError(response.status, body?.detail ?? body?.message ?? `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}
