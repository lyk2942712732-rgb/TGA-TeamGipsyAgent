---
name: crypto-and-encoding
version: "1"
modes: [ctf, binary_ctf]
capabilities: [workspace.read, workspace.write, workspace.python, artifact.inspect]
tags: [crypto, encoding, decoding]
---
# When to use
Use for an observed encoded blob, cipher text or deterministic transformation clue.

# Workflow
Preserve the original artifact, test a small reversible transformation in a workspace script, and save parameters and output. Treat a readable string as a lead until the task goal is verified.

# Stop or switch
Stop brute-force-like exploration when the budget is reached or no clue constrains the key space; request a hint or pursue a different artifact.
