export type RuntimeTab = "overview" | "work-items" | "timeline" | "evidence" | "resources" | "approvals" | "retrieval";
export type RuntimeSelection = { solverId: string | null; intentId: string | null; tab: RuntimeTab };

const TABS = new Set<RuntimeTab>(["overview", "work-items", "timeline", "evidence", "resources", "approvals", "retrieval"]);

export function readRuntimeSelection(search: string): RuntimeSelection {
  const params = new URLSearchParams(search);
  const tab = params.get("tab") as RuntimeTab | null;
  return {
    solverId: boundedId(params.get("solver")),
    intentId: boundedId(params.get("intent")),
    tab: tab && TABS.has(tab) ? tab : "overview",
  };
}

export function writeRuntimeSelection(
  search: string,
  patch: Partial<{ solverId: string | null; intentId: string | null; tab: RuntimeTab | null }>,
): string {
  const params = new URLSearchParams(search);
  set(params, "solver", patch.solverId);
  set(params, "intent", patch.intentId);
  set(params, "tab", patch.tab);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

function set(params: URLSearchParams, key: string, value: string | null | undefined): void {
  if (value === undefined) return;
  if (value === null || value === "" || (key === "tab" && value === "overview")) params.delete(key);
  else params.set(key, value);
}

function boundedId(value: string | null): string | null {
  return value && value.length <= 128 ? value : null;
}
