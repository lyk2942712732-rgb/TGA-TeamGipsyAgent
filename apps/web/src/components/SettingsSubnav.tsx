import { useNavigate } from "react-router-dom";
import { SETTINGS_SUBNAV_ITEMS } from "../app/navigation";
import type { AppPage } from "../app/router";

export function SettingsSubnav({ activePage }: { activePage: AppPage }) {
  const navigate = useNavigate();
  return (
    <nav className="settings-subnav" aria-label="设置分区">
      <span className="settings-subnav-title">SETTINGS</span>
      <div className="settings-subnav-tabs">
        {SETTINGS_SUBNAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const active = item.activePages.includes(activePage);
          return (
            <button
              key={item.id}
              type="button"
              className={`settings-subnav-btn${active ? " is-active" : ""}`}
              aria-current={active ? "page" : undefined}
              title={item.tooltip}
              onClick={() => navigate(item.path)}
            >
              <Icon size={14} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </div>
    </nav>
  );
}
