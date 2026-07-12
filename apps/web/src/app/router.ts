export type AppRoute = { page: "dashboard" | "new" | "runtime" | "replay" | "models" | "capabilities" | "skills"; taskId?: string };

export function readRoute(pathname = window.location.pathname): AppRoute {
  const parts = pathname.split("/").filter(Boolean);
  if (parts[0] === "tasks" && parts[1] && (parts[2] === "runtime" || parts[2] === "replay")) return { page: parts[2], taskId: decodeURIComponent(parts[1]) };
  if (parts[0] === "tasks" && parts[1] === "new") return { page: "new" };
  if (parts[0] === "settings" && ["models", "capabilities", "skills"].includes(parts[1] ?? "")) return { page: parts[1] as AppRoute["page"] };
  return { page: "dashboard" };
}
