export type AppTheme = "tungsten" | "swiss" | "spatial" | "instrument";
export type NavLayoutMode = "rail" | "topbar";

export const DEFAULT_THEME: AppTheme = "tungsten";
export const DEFAULT_NAV_MODE: NavLayoutMode = "rail";

const THEME_STORAGE_KEY = "tga3.ui.theme.v1";
const NAV_STORAGE_KEY = "tga3.ui.nav.v1";

const LEGACY_THEME_MAP: Record<string, AppTheme> = {
  obsidian: "tungsten",
  daylight: "swiss",
  cyber: "instrument",
};

export const APP_THEMES: ReadonlyArray<{
  id: AppTheme;
  code: string;
  label: string;
  short: string;
  swatch: string;
  swatchBorder?: string;
}> = [
  { id: "tungsten", code: "01", label: "钨钢测控", short: "钨钢", swatch: "#F05A28" },
  { id: "swiss", code: "02", label: "瑞士图纸", short: "图纸", swatch: "#F3F2EE", swatchBorder: "#D94214" },
  { id: "spatial", code: "03", label: "空间画布", short: "画布", swatch: "#38BDF8" },
  { id: "instrument", code: "04", label: "仪器终端", short: "终端", swatch: "#F59E0B" },
];

export const NAV_MODES: ReadonlyArray<{ id: NavLayoutMode; label: string; short: string }> = [
  { id: "rail", label: "48px 左侧极窄轨", short: "左侧轨" },
  { id: "topbar", label: "顶部极简导航栏", short: "顶部栏" },
];

export function isAppTheme(value: unknown): value is AppTheme {
  return value === "tungsten" || value === "swiss" || value === "spatial" || value === "instrument";
}

export function isNavLayoutMode(value: unknown): value is NavLayoutMode {
  return value === "rail" || value === "topbar";
}

export function normalizeTheme(value: unknown): AppTheme {
  if (isAppTheme(value)) return value;
  if (typeof value === "string" && value in LEGACY_THEME_MAP) return LEGACY_THEME_MAP[value];
  return DEFAULT_THEME;
}

export function loadTheme(): AppTheme {
  try {
    const saved = typeof window !== "undefined" ? window.localStorage?.getItem(THEME_STORAGE_KEY) : null;
    return normalizeTheme(saved);
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(theme: AppTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme === "swiss" ? "light" : "dark";
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("tga3:theme-changed", { detail: theme }));
  }
}

export function saveTheme(theme: AppTheme): void {
  applyTheme(theme);
  try {
    if (typeof window !== "undefined") {
      window.localStorage?.setItem(THEME_STORAGE_KEY, theme);
    }
  } catch {
    // Ignore storage restrictions in private mode.
  }
}

export function loadNavMode(): NavLayoutMode {
  try {
    const saved = typeof window !== "undefined" ? window.localStorage?.getItem(NAV_STORAGE_KEY) : null;
    return isNavLayoutMode(saved) ? saved : DEFAULT_NAV_MODE;
  } catch {
    return DEFAULT_NAV_MODE;
  }
}

export function applyNavMode(mode: NavLayoutMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.nav = mode;
}

export function saveNavMode(mode: NavLayoutMode): void {
  applyNavMode(mode);
  try {
    if (typeof window !== "undefined") {
      window.localStorage?.setItem(NAV_STORAGE_KEY, mode);
    }
  } catch {
    // Ignore storage restrictions in private mode.
  }
}
