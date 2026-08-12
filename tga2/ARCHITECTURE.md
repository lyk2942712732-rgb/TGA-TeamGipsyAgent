# TGA2 architecture

```text
apps/web -> apps/api -> tga2/bootstrap.Container
                         |-> tga2/agent (LangChain + LangGraph)
                         |-> tga2/core (product truth)
                         |-> tga2/integrations (model + MCP)
```

LangChain owns agent tool loops, structured output, retries, HITL approval and
Docker shell execution middleware. LangGraph owns topology, checkpoints,
interrupt and resume. TGA2 owns authorization, redaction, Artifact hashes,
EvidenceClaim/Finding validation, business events and reports.

There is one application container. API, CLI and tests receive the same Runtime,
configuration, Skill repository and MCP repository. Settings pages therefore
change the real execution objects instead of maintaining a display-only copy.
