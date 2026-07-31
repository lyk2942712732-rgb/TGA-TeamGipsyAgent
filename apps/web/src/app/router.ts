export type AppPage =
  | "dashboard"
  | "tasks"
  | "new"
  | "task-detail"
  | "runtime"
  | "replay"
  | "approvals"
  | "resources"
  | "reports"
  | "knowledge-bases"
  | "teams"
  | "solvers"
  | "skills"
  | "tools"
  | "models"
  | "policies"
  | "system"
  | "not-found";

export type AppRoute = {
  page: AppPage;
  taskId?: string;
};

const ROOT_ROUTES: Record<string, AppPage> = {
  approvals: "approvals",
  resources: "resources",
  reports: "reports",
  "knowledge-bases": "knowledge-bases",
  system: "system",
};

const SETTINGS_ROUTES: Record<string, AppPage> = {
  teams: "teams",
  solvers: "solvers",
  skills: "skills",
  tools: "tools",
  models: "models",
  policies: "policies",
};

export function readRoute(pathname = window.location.pathname): AppRoute {
  const parts = pathname.split("/").filter(Boolean);
  if (!parts.length) return { page: "dashboard" };

  if (parts[0] === "tasks") return readTaskRoute(parts);
  if (parts.length === 2 && parts[0] === "settings" && parts[1] && SETTINGS_ROUTES[parts[1]]) {
    return { page: SETTINGS_ROUTES[parts[1]] };
  }

  const rootPage = parts.length === 1 ? ROOT_ROUTES[parts[0]] : undefined;
  return rootPage ? { page: rootPage } : { page: "not-found" };
}

function readTaskRoute(parts: string[]): AppRoute {
  if (parts.length === 1) return { page: "tasks" };
  if (parts.length === 2 && parts[1] === "new") return { page: "new" };

  const taskId = decodeTaskId(parts[1]);
  if (!taskId) return { page: "not-found" };
  if (parts.length === 2) return { page: "task-detail", taskId };
  if (parts.length === 3 && (parts[2] === "runtime" || parts[2] === "replay")) {
    return { page: parts[2], taskId };
  }
  return { page: "not-found" };
}

function decodeTaskId(value: string): string | null {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
}

export function isRuntimePage(page: AppPage): boolean {
  return page === "runtime" || page === "replay";
}
