---
name: web-recon
version: "1"
modes: [ctf, penetration_test]
capabilities: [http.request, tool.invoke]
tags: [recon, links, forms, js]
---
# When to use
Use before exploit attempts when the HTTP surface is not evidenced.

# Workflow
Fetch the authorized landing page, record links, forms, hidden fields, script sources and API hints. Use a passive catalogued recon tool only when a concrete coverage gap remains.

# Stop or switch
Stop once the reachable entry points are enumerated. Turn each observed input into a testable hypothesis; do not keep path guessing.
