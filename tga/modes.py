"""Authoritative task-mode registry and legacy migration boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tga.contracts import TGATask


TaskMode = Literal[
    "ctf",
    "penetration_test",
    "incident_response",
    "vulnerability_research",
    "reverse_engineering",
]

TASK_MODES: tuple[TaskMode, ...] = (
    "ctf",
    "penetration_test",
    "incident_response",
    "vulnerability_research",
    "reverse_engineering",
)

LEGACY_MODE_MAP: dict[str, TaskMode] = {
    "ctf": "ctf",
    "web_audit": "penetration_test",
    "code_audit": "vulnerability_research",
    "binary_ctf": "reverse_engineering",
    **{mode: mode for mode in TASK_MODES},
}


@dataclass(frozen=True)
class ModeProfile:
    id: TaskMode
    label: str
    description: str
    methodology: tuple[str, ...]
    completion_focus: str
    observer_focus: str
    default_goal: str
    allowed_input_kinds: tuple[str, ...] = ("file", "archive", "image")
    required_conditions: tuple[str, ...] = ("goal", "task_files_or_hint")
    recommended_capabilities: tuple[str, ...] = ()
    completion_validator: str = ""
    report_sections: tuple[str, ...] = ()
    uses_flag: bool = False
    advanced_settings: tuple[str, ...] = ()

    def prompt(self) -> str:
        steps = "; ".join(self.methodology)
        return (
            f"Mode: {self.label} ({self.id}). Methodology: {steps}. "
            f"Completion focus: {self.completion_focus} Observer focus: {self.observer_focus}"
        )


MODE_PROFILES: dict[TaskMode, ModeProfile] = {
    "ctf": ModeProfile(
        id="ctf", label="CTF 解题",
        description="根据题面动态选择 Web、Pwn、Reverse、Crypto 或 Misc 路线，以真实证据验证 Flag。",
        methodology=("classify the challenge", "select evidence-producing tools", "recover and verify an Artifact-backed flag"),
        completion_focus="A valid, non-placeholder flag must be present in an Artifact owned by this task.",
        observer_focus="Track whether the chosen technical route is producing new evidence toward flag recovery.",
        default_goal="分析挑战并使用合适工具取得、验证真实 Flag。",
        recommended_capabilities=("http.request", "workspace.read", "artifact.inspect", "mcp"),
        completion_validator="ctf",
        report_sections=("challenge", "flags", "evidence", "limitations"),
        uses_flag=True,
        advanced_settings=("subtype", "verifier", "expected_flag_count", "deadline", "challenge_scope"),
    ),
    "penetration_test": ModeProfile(
        id="penetration_test", label="渗透测试",
        description="面向授权 Web、API、网络、主机、云或 AD 目标，验证攻击面、影响与覆盖范围。",
        methodology=("confirm scope", "map attack surface", "test vulnerability hypotheses", "preserve impact evidence", "report coverage and limitations"),
        completion_focus="Evidence-backed conclusions, explicit coverage, and limitations; finding a vulnerability is not required.",
        observer_focus="Watch coverage gaps, unsupported vulnerability claims, repeated scans, and untested impact assumptions.",
        default_goal="在授权范围内完成渗透测试，记录覆盖、证据、结论与限制。",
        recommended_capabilities=("http.request", "artifact.inspect", "mcp"),
        completion_validator="penetration_test",
        report_sections=("scope", "coverage", "findings", "evidence", "limitations", "remediation"),
        advanced_settings=("depth", "included_scopes", "exclusions", "rules_of_engagement", "testing_window", "rate_limit", "state_change"),
    ),
    "incident_response": ModeProfile(
        id="incident_response", label="应急响应",
        description="调查日志、流量、磁盘、内存、主机、云审计或恶意样本，优先保护原始证据。",
        methodology=("preserve evidence", "triage", "build timeline and IOCs", "analyze root cause and scope", "recommend containment and recovery"),
        completion_focus="The requested investigation questions must have evidence-backed conclusions and stated coverage.",
        observer_focus="Prioritize non-destructive analysis and flag unsupported IOC, attribution, root-cause, or impact claims.",
        default_goal="保全并分析相关证据，回答调查问题并给出处置与恢复建议。",
        recommended_capabilities=("workspace.read", "artifact.inspect", "mcp"),
        completion_validator="incident_response",
        report_sections=("timeline", "iocs", "affected_assets", "root_cause", "evidence", "containment", "recovery", "unresolved_risks"),
        advanced_settings=("phase", "time_range", "timezone", "evidence_preservation", "response_authority", "containment"),
    ),
    "vulnerability_research": ModeProfile(
        id="vulnerability_research", label="漏洞挖掘",
        description="开展源码、依赖、协议、模糊测试、Crash 分析和最小化复现。",
        methodology=("understand structure and attack surface", "form hypotheses", "combine static and dynamic validation", "minimize reproduction", "explain root cause and impact"),
        completion_focus="Vulnerability claims require reproduction evidence; negative results require coverage and limitations.",
        observer_focus="Watch for scanner-only claims, missing reproduction, untested preconditions, and uncovered components.",
        default_goal="分析目标并验证候选漏洞，记录复现证据、根因、影响、覆盖与限制。",
        recommended_capabilities=("workspace.read", "workspace.python", "artifact.inspect", "mcp"),
        completion_validator="vulnerability_research",
        report_sections=("target_version", "coverage", "findings", "reproduction", "root_cause", "impact", "artifacts", "limitations"),
        advanced_settings=("depth", "build_environment", "process_execution", "fuzzing", "poc", "disclosure_constraints"),
    ),
    "reverse_engineering": ModeProfile(
        id="reverse_engineering", label="逆向分析",
        description="分析二进制、固件、字节码或恶意样本，恢复用户要求的逻辑、行为、配置或数据。",
        methodology=("identify format and architecture", "perform static analysis", "use dynamic analysis when needed", "recover key logic and data structures", "save scripts and outputs"),
        completion_focus="The requested recovered logic, behavior, algorithm, configuration, or data must be backed by analysis Artifacts.",
        observer_focus="Watch for conclusions without disassembly, decompilation, execution output, or reproducible analysis scripts.",
        default_goal="逆向分析目标并以真实分析产物支撑所需的逻辑、行为或数据恢复结论。",
        allowed_input_kinds=("file", "files", "directory", "repository", "archive", "image", "artifact", "mcp_resource"),
        recommended_capabilities=("workspace.read", "artifact.inspect", "workspace.python", "mcp"),
        completion_validator="reverse_engineering",
        report_sections=("samples", "platform", "analysis", "key_functions", "behavior", "iocs", "outputs", "uncertainties"),
        advanced_settings=("analysis_method", "platform", "architecture", "sandbox", "process_execution", "network", "instrumentation"),
    ),
}


def normalize_mode(value: object) -> TaskMode:
    raw = str(value or "").strip()
    try:
        return LEGACY_MODE_MAP[raw]
    except KeyError as exc:
        raise ValueError(f"unsupported task mode: {raw}") from exc


def normalize_modes(values: list[str] | tuple[str, ...] | None) -> list[TaskMode]:
    source = list(values or TASK_MODES)
    return list(dict.fromkeys(normalize_mode(value) for value in source))


def mode_profile(mode: str) -> ModeProfile:
    return MODE_PROFILES[normalize_mode(mode)]


def is_task_mode(value: str) -> bool:
    return value in TASK_MODES


def validate_task_profile(task: "TGATask") -> None:
    """Backend-authoritative cross-field checks shared by every transport."""

    profile = mode_profile(task.mode)
    if task.schema_version >= 4:
        has_context = bool(task.session_input.task_files or task.session_input.hint.text or task.session_input.hint.files)
        if not has_context:
            raise ValueError("at least one task file, Hint text, or Hint attachment is required")
        invalid = []
    else:
        invalid = [item.kind for item in [*task.targets, *task.hints] if item.kind not in profile.allowed_input_kinds]
    if invalid and task.schema_version >= 3:
        raise ValueError(f"{task.mode} does not accept input kinds: {', '.join(sorted(set(invalid)))}")
    has_mcp = bool(task.schema_version < 4 and task.execution_policy and task.execution_policy.mcp.enabled_servers)
    if task.schema_version < 4 and not task.targets and not task.hints and not has_mcp:
        raise ValueError("at least one target, hint, or authorized MCP data source is required")
    if task.schema_version < 4 and task.mode in {"ctf", "penetration_test", "vulnerability_research", "reverse_engineering"} and not task.targets:
        raise ValueError(f"{task.mode} requires at least one target resource")

    policy = task.execution_policy
    config = task.mode_config
    if policy is None or config is None:
        raise ValueError("mode_config and execution_policy are required")
    if task.mode == "reverse_engineering":
        if getattr(config, "analysis_method", "") == "static_only" and policy.process_execution.mode != "forbidden":
            raise ValueError("reverse static_only requires process_execution=forbidden")
        if policy.process_execution.mode == "authorized_host" and getattr(config, "require_sandbox", True):
            raise ValueError("sandbox-required reverse analysis cannot authorize host execution")
    if task.mode == "vulnerability_research":
        if policy.fuzzing.mode != "disabled" and not getattr(config, "allow_fuzzing", False):
            raise ValueError("fuzzing policy cannot be enabled when mode_config disallows fuzzing")
        if policy.fuzzing.mode != "disabled" and (
            policy.fuzzing.max_cases <= 0 or policy.fuzzing.max_duration_seconds <= 0 or policy.fuzzing.concurrency <= 0
        ):
            raise ValueError("enabled fuzzing requires positive case, duration, and concurrency budgets")
    if policy.state_change.mode == "authorized" and not policy.state_change.allowed_actions:
        raise ValueError("authorized state changes require an explicit action allowlist")
    if policy.containment.mode == "authorized" and not policy.containment.allowed_actions:
        raise ValueError("authorized containment requires an explicit action allowlist")


def mode_profiles_payload() -> list[dict[str, Any]]:
    """Public contract used by the UI; defaults come from backend models."""

    from tga.contracts import (
        CtfModeConfig,
        ExecutionPolicy,
        IncidentResponseModeConfig,
        PenetrationTestModeConfig,
        ReverseAnalysisModeConfig,
        VulnerabilityResearchModeConfig,
        default_execution_policy,
    )

    config_types = {
        "ctf": CtfModeConfig,
        "penetration_test": PenetrationTestModeConfig,
        "incident_response": IncidentResponseModeConfig,
        "vulnerability_research": VulnerabilityResearchModeConfig,
        "reverse_engineering": ReverseAnalysisModeConfig,
    }
    values: list[dict[str, Any]] = []
    for mode in TASK_MODES:
        profile = MODE_PROFILES[mode]
        config_type = config_types[mode]
        values.append({
            "id": mode,
            "label": profile.label,
            "description": profile.description,
            "default_goal": profile.default_goal,
            "mode_config_schema": config_type.model_json_schema(),
            "default_mode_config": config_type().model_dump(mode="json"),
            "execution_policy_schema": ExecutionPolicy.model_json_schema(),
            "default_execution_policy": default_execution_policy(
                mode, targets=[], legacy_scope=[], intensity="passive",
            ).model_copy(update={"source": "default"}).model_dump(mode="json"),
            "allowed_input_kinds": list(profile.allowed_input_kinds),
            "required_conditions": list(profile.required_conditions),
            "recommended_capabilities": list(profile.recommended_capabilities),
            "prompt_instruction": profile.prompt(),
            "completion_validator": profile.completion_validator,
            "report_sections": list(profile.report_sections),
            "uses_flag": profile.uses_flag,
            "advanced_settings": list(profile.advanced_settings),
        })
    return values
