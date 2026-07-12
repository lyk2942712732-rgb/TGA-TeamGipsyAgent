---
name: web-vuln-triage
version: "1"
modes: [ctf, web_audit]
capabilities: [http.request, tool.invoke, artifact.inspect]
tags: [sqli, idor, upload, auth]
---
# When to use
Use only after an observed route, parameter, form or response behavior supports a concrete web hypothesis.

# Workflow
Make the smallest policy-approved request that distinguishes the hypothesis from a baseline. Preserve both results as artifacts and state which premise the result did or did not test.

# Stop or switch
After three semantically identical failures, record the failure boundary and select another evidence-backed route.
