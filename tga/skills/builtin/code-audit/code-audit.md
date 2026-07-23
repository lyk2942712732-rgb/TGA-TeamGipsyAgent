---
name: code-audit
version: "1"
modes: [vulnerability_research, ctf]
capabilities: [workspace.read, workspace.python, tool.invoke]
tags: [source, secrets, taint]
---
# When to use
Use for an authorized local source tree or challenge attachment.

# Workflow
Read only relevant files first, locate entry points and data flow, then use a catalogued scanner or a small workspace script for reproducible checks.

# Stop or switch
Do not equate a scanner hit with a finding. Switch to manual verification if there is no source-to-sink evidence.
