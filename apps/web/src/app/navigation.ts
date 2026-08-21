import {
  Activity,
  Bot,
  Boxes,
  CheckSquare,
  ClipboardCheck,
  FileBarChart,
  Gauge,
  Home,
  SlidersHorizontal,
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
      { id: "approvals", label: "审批中心", path: "/approvals", icon: ClipboardCheck, activePages: ["approvals"] },
      { id: "reports", label: "报告", path: "/reports", icon: FileBarChart, activePages: ["reports"] },
    ],
  },
  {
    id: "configuration",
    label: "配置中心",
    items: [
      { id: "solvers", label: "Solver", path: "/settings/solvers", icon: Bot, activePages: ["solvers"] },
      { id: "skills", label: "Skills", path: "/settings/skills", icon: Boxes, activePages: ["skills"] },
      { id: "models", label: "Models", path: "/settings/models", icon: SlidersHorizontal, activePages: ["models"], tooltip: "Provider 与模型" },
      { id: "policies", label: "场景", path: "/settings/policies", icon: Gauge, activePages: ["policies"] },
      { id: "system", label: "系统状态", path: "/system", icon: Activity, activePages: ["system"] },
    ],
  },
];

export function isNavigationItemActive(item: NavigationItem, page: AppPage): boolean {
  return item.activePages.includes(page);
}
