import { ApprovalCenter } from "../../approvals/ApprovalCenter";
import { EvidenceWorkspace } from "../../evidence/EvidenceWorkspace";
import { IntentBoard } from "../../intents/IntentBoard";
import { RetrievalPanel } from "../../retrieval/RetrievalPanel";
import type { RuntimeStore } from "../models/types";
import type { RuntimeTab } from "../runtime-selection";
import { ResourceWorkspace } from "./ResourceWorkspace";
import { TaskOverview } from "./TaskOverview";

const TABS: Array<[RuntimeTab, string]> = [["overview", "概览"], ["work-items", "工作项"], ["evidence", "证据"], ["findings", "发现"], ["resources", "资源"]];

export function TaskWorkspaceTabs({ store, tab, selectedIntentId, readonly = false, onChanged = () => undefined, onTab, onSolver = () => undefined, onIntent }: { store: RuntimeStore; tab: RuntimeTab; selectedSolverId: string | null; selectedIntentId: string | null; readonly?: boolean; onChanged?: () => void; onTab: (tab: RuntimeTab) => void; onSolver?: (solverId: string) => void; onIntent: (intentId: string) => void }) {
  const effectiveTab: RuntimeTab = TABS.some(([value]) => value === tab) ? tab : tab === "retrieval" ? "resources" : tab === "timeline" ? "work-items" : tab === "approvals" ? "approvals" : "overview";
  return <section className="task-workspace-tabs" aria-label="任务工作区"><div role="tablist" aria-label="任务工作区标签">{TABS.map(([value, label]) => <button key={value} role="tab" aria-selected={effectiveTab === value} aria-controls={`runtime-panel-${value}`} id={`runtime-tab-${value}`} onClick={() => onTab(value)}>{label}</button>)}</div><div role="tabpanel" id={`runtime-panel-${effectiveTab}`} tabIndex={0}>{effectiveTab === "overview" ? <TaskOverview store={store} onSelectSolver={onSolver} onSelectIntent={onIntent} /> : null}{effectiveTab === "work-items" ? <IntentBoard store={store} selectedIntentId={selectedIntentId} onSelect={onIntent} /> : null}{effectiveTab === "evidence" ? <EvidenceWorkspace store={store} initialTab="artifacts" /> : null}{effectiveTab === "findings" ? <EvidenceWorkspace store={store} initialTab="findings" /> : null}{effectiveTab === "resources" ? <><ResourceWorkspace store={store} /><RetrievalPanel store={store} /></> : null}{effectiveTab === "approvals" ? <ApprovalCenter store={store} readonly={readonly} onChanged={onChanged} /> : null}</div></section>;
}
