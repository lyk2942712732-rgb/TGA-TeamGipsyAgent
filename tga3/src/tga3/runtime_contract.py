"""Shared host-to-container runtime contract: environment variable names and workspace paths.

Keeping these constants in a zero-dependency leaf module allows both the host control plane
and containerized worker hooks/runners to reference the exact same contract without circular
imports or duplicated string literals.
"""

from __future__ import annotations

# Standard container mount roots
WORKSPACE_ROOT = "/workspace"
INPUTS_ROOT = "/inputs"
AOE_ROOT = "/aoe"
TASK_OVERVIEW_ROOT = "/task-overview"
FINDINGS_ROOT = "/findings"

# Standard files under .tga3/ or mounted roots
STATE_DIR_NAME = ".tga3"
COLLECTION_STATE_FILENAME = "collection-state.json"
SUBMISSION_CONTEXT_RELPATH = ".tga3/submission.json"
FINAL_CANDIDATE_FILENAME = "final-candidate.json"
SCOUT_PROBE_STATE_RELPATH = ".tga3/scout-probe-state.json"
OVERVIEW_READ_STATE_RELPATH = ".tga3/overview-read.json"
BLACKBOARD_ACK_RELPATH = ".tga3/blackboard-ack.json"

# Environment variable names passed from DockerContainerRuntime to worker containers
ENV_TASK_ID = "TGA3_TASK_ID"
ENV_AGENT_ID = "TGA3_AGENT_ID"
ENV_AGENT_ROLE = "TGA3_AGENT_ROLE"
ENV_AOE_PARENT_ID = "TGA3_AOE_PARENT_ID"
ENV_AOE_AUTO_SUBMIT_FLAG = "TGA3_AOE_AUTO_SUBMIT_FLAG"
ENV_AOE_ROOT = "TGA3_AOE_ROOT"
ENV_TASK_OVERVIEW_ROOT = "TGA3_TASK_OVERVIEW_ROOT"
ENV_WORKSPACE = "TGA3_WORKSPACE"
ENV_FINDINGS = "TGA3_FINDINGS"
ENV_ATTEMPT_ID = "TGA3_ATTEMPT_ID"
ENV_ATTEMPT_DEADLINE = "TGA3_ATTEMPT_DEADLINE"
ENV_BLACKBOARD_MCP_URL = "TGA3_BLACKBOARD_MCP_URL"
ENV_CONTROL_WS_URL = "TGA3_CONTROL_WS_URL"
ENV_SCOUT_PHASE = "TGA3_SCOUT_PHASE"

__all__ = [
    "AOE_ROOT",
    "BLACKBOARD_ACK_RELPATH",
    "COLLECTION_STATE_FILENAME",
    "ENV_AGENT_ID",
    "ENV_AGENT_ROLE",
    "ENV_AOE_AUTO_SUBMIT_FLAG",
    "ENV_AOE_PARENT_ID",
    "ENV_AOE_ROOT",
    "ENV_ATTEMPT_DEADLINE",
    "ENV_ATTEMPT_ID",
    "ENV_BLACKBOARD_MCP_URL",
    "ENV_CONTROL_WS_URL",
    "ENV_FINDINGS",
    "ENV_SCOUT_PHASE",
    "ENV_TASK_ID",
    "ENV_TASK_OVERVIEW_ROOT",
    "ENV_WORKSPACE",
    "FINAL_CANDIDATE_FILENAME",
    "FINDINGS_ROOT",
    "INPUTS_ROOT",
    "OVERVIEW_READ_STATE_RELPATH",
    "SCOUT_PROBE_STATE_RELPATH",
    "STATE_DIR_NAME",
    "SUBMISSION_CONTEXT_RELPATH",
    "TASK_OVERVIEW_ROOT",
    "WORKSPACE_ROOT",
]
