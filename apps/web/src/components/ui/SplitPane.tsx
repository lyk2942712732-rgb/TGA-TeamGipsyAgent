import type { ReactNode } from "react";

export function SplitPane({ primary, secondary, aside, className = "" }: { primary: ReactNode; secondary?: ReactNode; aside?: ReactNode; className?: string }) {
  return <div className={`split-pane ${secondary ? "has-secondary" : ""} ${aside ? "has-aside" : ""} ${className}`}>
    <section className="split-pane-primary">{primary}</section>
    {secondary ? <section className="split-pane-secondary">{secondary}</section> : null}
    {aside ? <aside className="split-pane-aside">{aside}</aside> : null}
  </div>;
}
