# TGA Agent Session safety model

The product runtime uses the Session target as its execution contract. It does
not ask the user to translate that target into scope entries, intensity levels,
risk labels, active-scan permission, or evidence-gate settings.

Operational boundaries are architectural:

- each Solver owns a private workspace and persistent transcript;
- the model can call only tools registered in the current Session;
- tool processes retain timeouts and output-size bounds needed to keep the
  host responsive;
- browser clients can request lifecycle actions but cannot execute host tools;
- secrets are not returned by configuration endpoints;
- cancellation and process recovery are explicit Session states.

Old scope/risk/budget/flag-gate fields may exist in saved v2 records for
backward readability. They are not product authorization gates in the native
AgentSession path.
