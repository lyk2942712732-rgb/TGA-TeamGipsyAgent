import { useState } from "react";
import { ArrowUpDown } from "lucide-react";
import { ApprovalCenter } from "../../approvals/ApprovalCenter";
import { EvidenceWorkspace } from "../../evidence/EvidenceWorkspace";
import { IntentBoard } from "../../intents/IntentBoard";
import { RetrievalPanel } from "../../retrieval/RetrievalPanel";
import { TimelinePanel } from "../../timeline/TimelinePanel";
import { useToast } from "../../../components/ui/Toast";
import type { RuntimeStore } from "../models/types";
import type { RuntimeTab } from "../runtime-selection";
import { ResourceWorkspace } from "./ResourceWorkspace";
import { TaskOverview } from "./TaskOverview";

// Labels follow reference image 05's Main Workspace tab strip.
const TABS: Array<[RuntimeTab, string]> = [["overview", "概览"], ["work-items", "Intent Board"], ["timeline", "时间线"], ["evidence", "证据库"], ["resources", "资源"], ["approvals", "审批中心"]];

export function TaskWorkspaceTabs({ store, tab, selectedSolverId, selectedIntentId, readonly = false, onChanged = () => undefined, onTab, onSolver = () => undefined, onIntent }: { store: RuntimeStore; tab: RuntimeTab; selectedSolverId: string | null; selectedIntentId: string | null; readonly?: boolean; onChanged?: () => void; onTab: (tab: RuntimeTab) => void; onSolver?: (solverId: string) => void; onIntent: (intentId: string) => void }) {
  const toast = useToast();
  const [compact, setCompact] = useState(false);
  const effectiveTab = TABS.some(([value]) => value === tab) ? tab : tab === "retrieval" ? "resources" : "overview";

  return <section className="task-workspace-tabs" aria-label="任务工作区">
    <header><h2>Main Workspace</h2></header>
    <div className="workspace-tabstrip">
      <div role="tablist" aria-label="任务工作区标签">
        {TABS.map(([value, label]) => <button
          key={value}
          role="tab"
          aria-selected={effectiveTab === value}
          aria-controls={`runtime-panel-${value}`}
          id={`runtime-tab-${value}`}
          onClick={() => onTab(value)}
        >{label}</button>)}
      </div>
      <div className="workspace-tabstrip-tools">
        {/* Real density switch: it tightens the work-item cards in place. */}
        <label className="workspace-compact-switch">
          <input type="checkbox" checked={compact} onChange={(event) => setCompact(event.target.checked)} />
          <i aria-hidden="true" />紧凑视图
        </label>
        <button type="button" className="workspace-icon-button" aria-label="排序" onClick={() => toast.notifyUnavailable("工作区排序")}>
          <ArrowUpDown size={15} />
        </button>
      </div>
    </div>
    <div
      role="tabpanel"
      className="workspace-panel"
      data-compact={compact}
      id={`runtime-panel-${effectiveTab}`}
      aria-labelledby={`runtime-tab-${effectiveTab}`}
      tabIndex={0}
    >
      {effectiveTab === "overview" ? <TaskOverview store={store} onSelectSolver={onSolver} onSelectIntent={onIntent} /> : null}
      {effectiveTab === "work-items" ? <IntentBoard store={store} selectedIntentId={selectedIntentId} onSelect={onIntent} /> : null}
      {effectiveTab === "timeline" ? <TimelinePanel store={store} solverId={selectedSolverId} intentId={selectedIntentId} /> : null}
      {effectiveTab === "evidence" ? <EvidenceWorkspace store={store} /> : null}
      {effectiveTab === "resources" ? <><ResourceWorkspace store={store} /><RetrievalPanel store={store} /></> : null}
      {effectiveTab === "approvals" ? <ApprovalCenter store={store} readonly={readonly} onChanged={onChanged} /> : null}
    </div>
  </section>;
}
