---
name: binary-triage
version: "1"
modes: [binary_ctf, ctf]
capabilities: [workspace.read, workspace.python, tool.invoke]
tags: [binary, strings, metadata]
---
# When to use
Use for an authorized binary or forensic sample in the solver workspace.

# Workflow
Start with metadata, strings and file structure. Keep generated extracts in the private workspace and retain tool outputs as artifacts.

# Stop or switch
If static evidence is exhausted, record exactly what was checked before changing to a dynamic or reverse-engineering hypothesis.
