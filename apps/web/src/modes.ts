export const TASK_MODES = ["penetration_test", "incident_response", "vulnerability_research", "reverse_engineering", "pwn", "security_misc", "cryptography", "forensics"] as const;
export type TaskMode = typeof TASK_MODES[number];

export const TASK_MODE_MAP: Record<string, TaskMode> = {
  penetration_test: "penetration_test",
  incident_response: "incident_response",
  vulnerability_research: "vulnerability_research",
  reverse_engineering: "reverse_engineering",
  pwn: "pwn",
  security_misc: "security_misc",
  cryptography: "cryptography",
  forensics: "forensics",
};

export const MODE_PROFILES: Record<TaskMode, { label: string; description: string; defaultGoal: string }> = {
  penetration_test: { label: "渗透测试", description: "面向授权 Web、API、网络、主机、云或 AD 目标，验证攻击面、影响与覆盖范围。", defaultGoal: "在授权范围内完成渗透测试，记录覆盖、证据、结论与限制。" },
  incident_response: { label: "应急响应", description: "调查日志、流量、磁盘、内存、主机、云审计或恶意样本，优先保护原始证据。", defaultGoal: "保全并分析相关证据，回答调查问题并给出处置与恢复建议。" },
  vulnerability_research: { label: "漏洞挖掘", description: "开展源码、依赖、协议、模糊测试、Crash 分析和最小化复现。", defaultGoal: "分析目标并验证候选漏洞，记录复现证据、根因、影响、覆盖与限制。" },
  reverse_engineering: { label: "逆向分析", description: "分析二进制、固件、字节码或混淆逻辑并恢复 Flag。", defaultGoal: "恢复关键逻辑、输入约束或隐藏数据，并验证 Flag。" },
  pwn: { label: "Pwn", description: "分析二进制保护和内存破坏原语，构造稳定利用。", defaultGoal: "构造可复现利用链并取得、验证 Flag。" },
  security_misc: { label: "安全杂项", description: "处理隐写、编码、协议、脚本和综合安全题。", defaultGoal: "识别题目机制并提取、验证 Flag。" },
  cryptography: { label: "密码", description: "分析密码原语、协议缺陷和错误实现。", defaultGoal: "恢复明文或密钥并提取、验证 Flag。" },
  forensics: { label: "取证", description: "从磁盘、内存、流量、日志和媒体中恢复证据。", defaultGoal: "沿可审计证据链定位、恢复并验证 Flag。" },
};

export function normalizeTaskMode(value: unknown): TaskMode {
  return TASK_MODE_MAP[String(value ?? "")] ?? "penetration_test";
}
