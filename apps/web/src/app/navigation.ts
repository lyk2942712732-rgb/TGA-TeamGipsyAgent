import {
  Activity,
  Archive,
  Bot,
  Boxes,
  CheckSquare,
  FileBarChart,
  Gauge,
  Home,
  Library,
  Network,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import type { AppPage } from "./router";

export type NavigationItem = {
  id: string;
  label: string;
  path: string;
  icon: LucideIcon;
  activePages: AppPage[];
  tooltip?: string;
};

export type NavigationGroup = {
  id: "workspace" | "configuration";
  label: string;
  items: NavigationItem[];
};

export const NAVIGATION_GROUPS: NavigationGroup[] = [
  {
    id: "workspace",
    label: "工作区",
    items: [
      { id: "home", label: "首页", path: "/", icon: Home, activePages: ["dashboard"] },
      { id: "tasks", label: "任务", path: "/tasks", icon: CheckSquare, activePages: ["tasks", "new", "task-detail", "runtime", "replay"] },
      { id: "approvals", label: "审批", path: "/approvals", icon: ShieldCheck, activePages: ["approvals"] },
      { id: "resources", label: "资源", path: "/resources", icon: Archive, activePages: ["resources"] },
      { id: "reports", label: "报告", path: "/reports", icon: FileBarChart, activePages: ["reports"] },
      { id: "knowledge", label: "知识库", path: "/knowledge-bases", icon: Library, activePages: ["knowledge-bases"] },
    ],
  },
  {
    id: "configuration",
    label: "配置中心",
    items: [
      { id: "teams", label: "团队模板", path: "/settings/teams", icon: Network, activePages: ["teams"] },
      { id: "solvers", label: "Solver", path: "/settings/solvers", icon: Bot, activePages: ["solvers"] },
      { id: "skills", label: "Skills", path: "/settings/skills", icon: Boxes, activePages: ["skills"] },
      { id: "tools", label: "Tools & MCP", path: "/settings/tools", icon: Wrench, activePages: ["tools"] },
      { id: "models", label: "Models", path: "/settings/models", icon: SlidersHorizontal, activePages: ["models"], tooltip: "Provider 与模型" },
      { id: "policies", label: "策略与预算", path: "/settings/policies", icon: Gauge, activePages: ["policies"] },
      { id: "system", label: "系统状态", path: "/system", icon: Activity, activePages: ["system"] },
    ],
  },
];

export function isNavigationItemActive(item: NavigationItem, page: AppPage): boolean {
  return item.activePages.includes(page);
}
