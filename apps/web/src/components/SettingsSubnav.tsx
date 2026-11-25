import { useNavigate } from "react-router-dom";
import { SETTINGS_SUBNAV_ITEMS } from "../app/navigation";
import type { AppPage } from "../app/router";

export function SettingsSubnav({ activePage }: { activePage: AppPage }) {
  const navigate = useNavigate();
  return (
    <nav className="settings-nav" aria-label="设置分区">
      {SETTINGS_SUBNAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const active = item.activePages.includes(activePage);
        return (
          <button
            key={item.id}
            type="button"
            className={`settings-nav-item${active ? " is-active" : ""}`}
            aria-current={active ? "page" : undefined}
            title={item.tooltip}
            onClick={() => navigate(item.path)}
          >
            <span className="settings-nav-label">
              <Icon size={14} aria-hidden="true" />
              <span>{item.label}</span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
