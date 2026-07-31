export type DetailTab = {
  id: string;
  label: string;
  /** Reference design shows the tab but no API backs its content. */
  missing?: boolean;
};

/** Underlined tab strip used inside detail panels and page headers. */
export function DetailTabs({ tabs, active, onSelect, size = "md" }: {
  tabs: readonly DetailTab[];
  active: string;
  onSelect: (id: string) => void;
  size?: "md" | "lg";
}) {
  return <div className={`detail-tabs size-${size}`} role="tablist">
    {tabs.map((tab) => <button
      key={tab.id}
      role="tab"
      type="button"
      aria-selected={tab.id === active}
      className={tab.id === active ? "active" : ""}
      onClick={() => onSelect(tab.id)}
    >
      {tab.label}
    </button>)}
  </div>;
}
