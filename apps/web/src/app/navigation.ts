import {
  Activity,
  Bot,
  Boxes,
  CheckSquare,
  FileBarChart,
  Gauge,
  LayoutGrid,
  Plus,
  Settings,
  SlidersHorizontal,
  type LucideIcon,
} from "lucide-react";
import type { AppPage } from "./router";

export type NavigationItem = {
  id: string;
  label: string;
  shortLabel?: string;
  path: string;
  icon: LucideIcon;
  activePages: AppPage[];
  tooltip?: string;
  accent?: boolean;
};

export type NavigationGroup = {
  id: "primary" | "bottom";
  label: string;
  items: NavigationItem[];
};

export const PRIMARY_NAV_ITEMS: NavigationItem[] = [
  { id: "home", label: "总览", shortLabel: "总览", path: "/", icon: LayoutGrid, activePages: ["dashboard"], tooltip: "作战总览" },
  { id: "tasks", label: "任务", shortLabel: "任务库", path: "/tasks", icon: CheckSquare, activePages: ["tasks", "runtime"], tooltip: "任务调度与作战室" },
  { id: "reports", label: "复盘库", shortLabel: "复盘库", path: "/reports", icon: FileBarChart, activePages: ["reports"], tooltip: "Writeup 报告与经验卡库" },
  { id: "skills", label: "武器库", shortLabel: "武器库", path: "/settings/skills", icon: Boxes, activePages: ["skills"], tooltip: "Skills 能力包 IDE" },
];

export const BOTTOM_NAV_ITEMS: NavigationItem[] = [
  { id: "new", label: "新建任务", shortLabel: "+ 新建", path: "/tasks/new", icon: Plus, activePages: ["new"], tooltip: "新建任务发射台", accent: true },
  { id: "settings", label: "设置", shortLabel: "设置", path: "/settings/models", icon: Settings, activePages: ["models", "solvers", "policies", "system"], tooltip: "统一设置中心" },
];

export const SETTINGS_SUBNAV_ITEMS: NavigationItem[] = [
  { id: "models", label: "模型与网关", path: "/settings/models", icon: SlidersHorizontal, activePages: ["models"], tooltip: "Provider、协议端点与 API Key" },
  { id: "solvers", label: "舰队装配", path: "/settings/solvers", icon: Bot, activePages: ["solvers"], tooltip: "Scout / 4 Workers / Reporter CLI 与 Prompt" },
  { id: "policies", label: "场景与策略", path: "/settings/policies", icon: Gauge, activePages: ["policies"], tooltip: "7 类场景系统提示词" },
  { id: "system", label: "系统诊断", path: "/system", icon: Activity, activePages: ["system"], tooltip: "控制面、容器镜像与组件健康" },
];

export const NAVIGATION_GROUPS: NavigationGroup[] = [
  { id: "primary", label: "工作区", items: PRIMARY_NAV_ITEMS },
  { id: "bottom", label: "配置与操作", items: BOTTOM_NAV_ITEMS },
];

export function isNavigationItemActive(item: NavigationItem, page: AppPage): boolean {
  return item.activePages.includes(page);
}
