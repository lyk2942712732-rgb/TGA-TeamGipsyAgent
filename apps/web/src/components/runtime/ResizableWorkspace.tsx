import { ReactNode, useEffect, useState } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";

type WorkspaceTab = "board" | "timeline" | "evidence";

function useCompactRuntime() {
  const query = "(max-width: 1024px)";
  const [compact, setCompact] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setCompact(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  return compact;
}

/** Desktop panels are resizable; smaller screens switch to one reachable tab at a time. */
export function ResizableWorkspace({ board, timeline, evidence }: { board: ReactNode; timeline: ReactNode; evidence: ReactNode }) {
  const compact = useCompactRuntime();
  const [tab, setTab] = useState<WorkspaceTab>("timeline");
  if (compact) {
    const content = tab === "board" ? board : tab === "timeline" ? timeline : evidence;
    return <section className="runtime-compact-workspace">
      <div className="runtime-workspace-tabs" role="tablist" aria-label="Runtime 面板">
        {([ ["board", "策略板"], ["timeline", "时间线"], ["evidence", "证据与结果"] ] as [WorkspaceTab, string][]).map(([value, label]) => <button key={value} role="tab" aria-selected={tab === value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>{label}</button>)}
      </div>
      <div className="runtime-compact-panel">{content}</div>
    </section>;
  }
  return <Group className="runtime-panel-group" orientation="horizontal" id="runtime-workspace" defaultLayout={{ board: 27, timeline: 43, evidence: 30 }}>
    <Panel id="board" minSize="230px">{board}</Panel>
    <Separator className="runtime-panel-separator" aria-label="调整策略板宽度" />
    <Panel id="timeline" minSize="330px">{timeline}</Panel>
    <Separator className="runtime-panel-separator" aria-label="调整证据面板宽度" />
    <Panel id="evidence" minSize="250px">{evidence}</Panel>
  </Group>;
}
