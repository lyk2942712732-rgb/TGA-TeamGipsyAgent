# BreachWeave runtime UI alignment

## Observed failure in the previous TGA page

The live task `task_308df832b8fd` made the previous layout failure concrete:

- the page expanded to several viewport heights because the action graph used a
  fixed column canvas whose height grew with every action;
- Hypotheses, raw event cards, and Artifacts were rendered as three independent
  scrolling databases below the graph;
- saturated purple blocks, large text, unbroken identifiers, and repeated labels
  dominated the evidence hierarchy;
- Solver routing and runtime activity were separated from the graph, so users had
  to correlate them manually;
- hints permanently consumed a full-width row even when no human intervention was
  needed.

## Adopted reference layout

The runtime page now follows the reference product's attack-flow workspace rather
than the legacy TGA page structure:

```text
compact challenge header + controls
┌──────────────────────┬──────────────────────────────────┐
│ ordered activity     │ shared Ideas / Memory graph      │
│ filters + Solver lane├──────────────────────────────────┤
│                      │ Manager / Solver / Action graph   │
└──────────────────────┴──────────────────────────────────┘
replay / live cursor / speed
```

- The entire workbench is exactly one viewport high. Only the Activity lane scrolls.
- Both graphs use React Flow for fit-to-view, pan, zoom, animated active edges, and
  click-through inspection.
- Event, Solver, Idea, Memory, Action, and Artifact details open in a side drawer.
- Evidence and confirmed results use a dedicated drawer instead of a permanent
  database column.
- Human hints use a focused dialog and continue to call only the Manager API.
- At narrower widths the same data becomes Activity / Knowledge / Topology tabs;
  no alternative runtime is introduced.

All nodes and events are projections of the v2 snapshot and ordered event stream.
No visual demo records are synthesized.

## Runtime correction discovered during visual validation

The same live task stopped with `solver_action_budget_exhausted` after a child
Solver consumed an eight-action work packet. That local packet was incorrectly
treated as a challenge-level terminal condition.

The Manager now:

- treats a subagent request's packet size as scheduling metadata;
- enforces the configured per-Solver hard allowance in both Manager and executor;
- falls back to the durable main Solver when a child reaches its allowance;
- allows controlled environment limits to be raised within explicit hard caps;
- lets recovered sessions adopt a raised turn allowance.

Scope, capability authorization, rate limits, semantic-repeat protection, evidence
provenance, and completion gates remain enforced.

## Acceptance checks

- 1440 x 1000: Activity and both graphs are simultaneously visible.
- 1024 x 800 and 768 x 900: tabbed panes fit without document overflow.
- Replay changes only the event cursor and never re-executes a capability.
- Evidence-free flags are never promoted to the confirmed-results drawer.
- Clicking a real event/node opens recorded details and artifact links.
