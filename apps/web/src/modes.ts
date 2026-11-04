export const TASK_MODES = ["penetration_test", "pwn", "reverse_engineering", "security_misc", "cryptography", "ai_security", "aoe"] as const;
export type TaskMode = typeof TASK_MODES[number];

export const TASK_MODE_MAP: Record<string, TaskMode> = {
  penetration_test: "penetration_test",
  reverse_engineering: "reverse_engineering",
  pwn: "pwn",
  security_misc: "security_misc",
  cryptography: "cryptography",
  ai_security: "ai_security",
  aoe: "aoe",
};

export const MODE_PROFILES: Record<TaskMode, { label: string; description: string; defaultGoal: string }> = {
  penetration_test: { label: "Web渗透测试", description: "分析 Web 应用、API、认证与输入边界，验证可利用路径。", defaultGoal: "在题目范围内验证 Web 利用链，取得并验证答案或 Flag。" },
  reverse_engineering: { label: "Re逆向", description: "分析二进制、固件、字节码或混淆逻辑并恢复 Flag。", defaultGoal: "恢复关键逻辑、输入约束或隐藏数据，并验证 Flag。" },
  pwn: { label: "Pwn", description: "分析二进制保护和内存破坏原语，构造稳定利用。", defaultGoal: "构造可复现利用链并取得、验证 Flag。" },
  security_misc: { label: "Misc安全杂项", description: "分析隐写、编码、取证、流量、媒体和综合型安全题。", defaultGoal: "识别题目机制并提取、验证 Flag。" },
  cryptography: { label: "Crypto密码学问题", description: "分析密码原语、协议缺陷和错误实现。", defaultGoal: "恢复明文或密钥并提取、验证 Flag。" },
  ai_security: { label: "AI安全", description: "分析模型、提示词、RAG 与智能体应用的安全题。", defaultGoal: "验证题目中的 AI 安全缺陷，取得并验证答案或 Flag。" },
  aoe: { label: "AOE多题", description: "收集整批题目，按题共享情报、分配两个 Worker 并逐题生成报告。", defaultGoal: "逐题验证答案，保存独立黑板、执行记录和解题报告。" },
};

// Historical tasks keep their original category; these are not new-task choices.
const LEGACY_SCENE_LABELS: Record<string, string> = {
  incident_response: "应急响应", vulnerability_research: "漏洞挖掘", forensics: "取证",
};

export function sceneLabel(value: string): string {
  return MODE_PROFILES[value as TaskMode]?.label ?? LEGACY_SCENE_LABELS[value] ?? value;
}

export function normalizeTaskMode(value: unknown): TaskMode {
  return TASK_MODE_MAP[String(value ?? "")] ?? "penetration_test";
}
