const configuredApiBase = import.meta.env.VITE_TGA_API_BASE as string | undefined;
export const apiBase = (configuredApiBase ?? "http://127.0.0.1:8000").replace(/\/$/, "");

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
