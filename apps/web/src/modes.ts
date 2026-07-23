export const TASK_MODES = ["ctf", "penetration_test", "incident_response", "vulnerability_research", "reverse_engineering"] as const;
export type TaskMode = typeof TASK_MODES[number];

export const LEGACY_MODE_MAP: Record<string, TaskMode> = {
  ctf: "ctf",
  web_audit: "penetration_test",
  code_audit: "vulnerability_research",
  binary_ctf: "reverse_engineering",
  penetration_test: "penetration_test",
  incident_response: "incident_response",
  vulnerability_research: "vulnerability_research",
  reverse_engineering: "reverse_engineering",
};

export const MODE_PROFILES: Record<TaskMode, { label: string; description: string; defaultGoal: string }> = {
  ctf: { label: "CTF 解题", description: "根据题面动态选择 Web、Pwn、Reverse、Crypto 或 Misc 路线，并验证真实 Flag。", defaultGoal: "分析挑战并使用合适工具取得、验证真实 Flag。" },
  penetration_test: { label: "渗透测试", description: "面向授权 Web、API、网络、主机、云或 AD 目标，验证攻击面、影响与覆盖范围。", defaultGoal: "在授权范围内完成渗透测试，记录覆盖、证据、结论与限制。" },
  incident_response: { label: "应急响应", description: "调查日志、流量、磁盘、内存、主机、云审计或恶意样本，优先保护原始证据。", defaultGoal: "保全并分析相关证据，回答调查问题并给出处置与恢复建议。" },
  vulnerability_research: { label: "漏洞挖掘", description: "开展源码、依赖、协议、模糊测试、Crash 分析和最小化复现。", defaultGoal: "分析目标并验证候选漏洞，记录复现证据、根因、影响、覆盖与限制。" },
  reverse_engineering: { label: "逆向分析", description: "分析二进制、固件、字节码或恶意样本，恢复所需逻辑、行为、配置或数据。", defaultGoal: "逆向分析目标并以真实分析产物支撑所需的逻辑、行为或数据恢复结论。" },
};

export function normalizeTaskMode(value: unknown): TaskMode {
  return LEGACY_MODE_MAP[String(value ?? "")] ?? "ctf";
}
