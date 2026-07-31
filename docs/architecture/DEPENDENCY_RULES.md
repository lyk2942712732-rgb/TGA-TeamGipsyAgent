# Dependency rules

1. `apps` depend on `application`; `application` may depend on `runtime` and `domain`; `runtime` depends on domain and application ports.
2. Infrastructure implements application ports. Domain code never imports FastAPI, SQLite, provider SDKs, or MCP transports.
3. API routes never perform direct SQL or construct persistence stores.
4. Runtime opens only the owning Task database through current persistence services.
5. Model text, hints, skills, and retrieval results never expand authorization or completion authority.
6. Transcript, Event, Artifact, EvidenceClaim, Finding, and Knowledge remain distinct concepts.
7. Public contracts export current models only. Historical models and readers are confined to `tga.migrations`.

`tests/test_architecture_boundaries.py` enforces domain imports and public model identity.
