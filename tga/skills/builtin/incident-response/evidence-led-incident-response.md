---
name: evidence-led-incident-response
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect, workspace.python]
tags: [incident-response, timeline, ioc, forensics, evidence]
version: "1"
---

Preserve original evidence and record hashes or source references before analysis. Prefer read-only inspection and derived working copies. Build a bounded timeline, distinguish observed IOCs from hypotheses, and cite task-owned Artifacts for root-cause, attack-path, and impact conclusions. Record investigation coverage, unavailable evidence, containment recommendations, and recovery limitations without modifying or cleaning the source evidence.
