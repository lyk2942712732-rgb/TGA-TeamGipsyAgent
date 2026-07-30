# Dependency rules

1. `apps` may depend on `application`; `application` may depend on `runtime` and
   `domain`; `runtime` may depend on `domain` and application ports.
2. Infrastructure implements application ports. Domain code never imports
   FastAPI, SQLite, provider SDKs or MCP transports.
3. API routes never perform direct SQL. New persistence construction goes
   through the composition root and later repository adapters.
4. Runtime code must not open unrelated databases ad hoc; remaining
   `EvidenceStore` dependencies are tracked migration debt.
5. Model-controlled text, hints, skills and retrieval results never expand
   authorization or completion authority.
6. Transcripts, events, artifacts, evidence claims and knowledge are separate
   concepts and storage projections.
7. Compatibility modules may re-export canonical objects, but must not redefine
   them. JSON contracts remain stable while compatibility is supported.

`tests/test_architecture_boundaries.py` enforces the domain import boundary and
compatibility identity rather than relying on this document alone.

