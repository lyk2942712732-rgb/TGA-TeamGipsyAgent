import type { RuntimeSnapshot } from "../../runtime/event-types";

export function GovernanceOverview({ snapshot }: { snapshot: RuntimeSnapshot }) {
  const cards = snapshot.board.strategy_cards ?? [];
  const card = cards.find((item) => item.active_step_id) ?? cards[cards.length - 1];
  const activeStep = card?.steps.find((item) => item.id === card.active_step_id);
  const linkedActions = activeStep ? snapshot.actions.filter((item) => item.strategy_step_id === activeStep.id) : [];
  const hintSources = card?.sources ?? [];
  const extracted = hintSources.filter((item) => item.extraction_status === "extracted").length;
  const blocked = hintSources.filter((item) => ["failed", "blocked_out_of_scope"].includes(item.extraction_status)).length;
  const sessions = snapshot.http_sessions ?? [];
  const metrics = snapshot.context_metrics ?? [];
  const directives = snapshot.observer?.directives ?? [];
  const http = sessions[sessions.length - 1];
  const metric = metrics[metrics.length - 1];
  const directive = directives[directives.length - 1];
  const directiveText = typeof directive?.steer_message === "string" ? directive.steer_message : "";

  return <section className="governance-overview" aria-label="Runtime governance">
    <article data-testid="strategy-overview">
      <header><span>Hint / Strategy</span><b>{card?.status ?? "not structured"}</b></header>
      <p>{card?.title ?? "No StrategyCard has been persisted for this historical task."}</p>
      <small>{hintSources.length} sources · {extracted} extracted · {blocked} blocked/failed</small>
      {activeStep ? <div className="strategy-current"><b>{activeStep.title}</b><span>{activeStep.expected_request || "evidence-producing validation"}</span><small>{linkedActions.length} actions · {activeStep.evidence_artifact_ids.length} evidence Artifacts</small></div> : null}
    </article>
    <article data-testid="observer-overview">
      <header><span>Manager / Observer</span><b>{directiveText ? "directive active" : "watching"}</b></header>
      <p>{directiveText || "No corrective directive has been emitted."}</p>
      <small>Timeline separates model plans, actual tool results, Observer advice and system rejection.</small>
    </article>
    <article data-testid="http-session-overview">
      <header><span>HTTP Session</span><b>{http?.profile ?? "not used"}</b></header>
      <p>{http ? `${http.origin_count} isolated origins · ${http.request_count} requests · ${http.rebuild_count} rebuilds` : "No HTTP profile metadata yet."}</p>
      <small>Cookie values are never exposed · cross-process recovery {http?.cross_process_recovery ? "enabled" : "disabled"}</small>
    </article>
    <article data-testid="context-overview">
      <header><span>Working Context</span><b>{metric ? `${metric.working_chars.toLocaleString()} chars` : "not measured"}</b></header>
      <p>{metric ? `${metric.working_message_count}/${metric.audit_message_count} messages projected · ${metric.summary_hits} compacted results` : "Waiting for the first model turn."}</p>
      <small>{metric?.artifact_retrievals ?? 0} structured Artifact retrievals</small>
    </article>
  </section>;
}
