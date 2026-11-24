export type AppTheme = "obsidian" | "daylight" | "cyber";

export const DEFAULT_THEME: AppTheme = "obsidian";
const STORAGE_KEY = "tga3.ui.theme.v1";

export const APP_THEMES: ReadonlyArray<{ id: AppTheme; label: string; short: string }> = [
  { id: "obsidian", label: "曜石暗色", short: "暗色" },
  { id: "daylight", label: "极简亮色", short: "亮色" },
  { id: "cyber", label: "终端极客", short: "极客" },
];

export function isAppTheme(value: unknown): value is AppTheme {
  return value === "obsidian" || value === "daylight" || value === "cyber";
}

export function loadTheme(): AppTheme {
  try {
    const saved = typeof window !== "undefined" ? window.localStorage?.getItem(STORAGE_KEY) : null;
    return isAppTheme(saved) ? saved : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function applyTheme(theme: AppTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme === "daylight" ? "light" : "dark";
}

export function saveTheme(theme: AppTheme): void {
  applyTheme(theme);
  try {
    if (typeof window !== "undefined") {
      window.localStorage?.setItem(STORAGE_KEY, theme);
    }
  } catch {
    // Ignore storage restrictions in private mode.
  }
}
