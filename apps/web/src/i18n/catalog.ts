/**
 * Chinese display layer for the catalog's English identifiers.
 *
 * The backend stores Solver definitions, Skills and their prompt templates in
 * English; nothing here rewrites that data.  These maps only translate what the
 * UI prints, so the console reads as one product in one language.  Anything not
 * listed falls through to the original string rather than being dropped, and the
 * untouched source text stays reachable behind 查看完整模板 / 编辑.
 */

/** Solver specialties and Skill tags share one vocabulary. */
const TERMS: Record<string, string> = {
  // orchestration
  coordination: "任务协调", planning: "全局规划", "completion-governance": "完成治理",
  "evidence-review": "证据复核", "knowledge-promotion": "知识晋级", "conflict-resolution": "冲突消解",
  reporting: "报告撰写", "coverage-summary": "覆盖总结", "evidence-citation": "证据引用",
  // recon / web
  recon: "信息侦察", "surface-triage": "攻击面分诊", web: "Web 分析", network: "网络分析",
  "http-validation": "HTTP 验证", links: "链接发现", forms: "表单分析", js: "脚本分析",
  // vulnerability
  validation: "结论验证", reproduction: "复现验证", "negative-testing": "反证测试",
  sqli: "SQL 注入", idor: "越权访问", upload: "文件上传", auth: "认证绕过",
  // code / binary
  source: "源码审计", "static-analysis": "静态分析", "data-flow": "数据流分析",
  secrets: "凭据泄露", taint: "污点追踪", binary: "二进制分析", reverse: "逆向工程",
  strings: "字符串提取", metadata: "元数据分析",
  // crypto
  crypto: "密码分析", encoding: "编码识别", decoding: "解码还原",
  // forensics / IR
  forensics: "取证分析", timeline: "时间线重建", ioc: "威胁指标", evidence: "证据管理",
  "incident-response": "应急响应",
};

export function termLabel(value: string): string {
  return TERMS[value] ?? value;
}

/** Reference 11's 描述 line for each shipped Solver definition. */
const SOLVER_DESCRIPTIONS: Record<string, string> = {
  "binary-analysis": "在私有工作区内执行受限的二进制分析，产出可复现的观察结论、候选论断、覆盖范围与已知局限。",
  "code-audit": "针对指派的 Intent 追踪源码入口与数据流，保留可复现的产物与证据线索。",
  "evidence-reviewer": "比对产物复核候选证据与知识，驳回缺乏支撑的结论，并记录冲突消解过程。",
  "forensics-analysis": "保全原始证据，围绕指派的 Intent 构建受限时间线，返回候选知识与取证结论。",
  "recon-triage": "对指派的 Intent 执行受限侦察，返回已覆盖范围、产物、局限与后续建议。",
  "security-reporter": "仅依据已验证的任务知识与已确认证据生成报告，并附上覆盖范围与局限说明。",
  "task-supervisor": "统筹任务目标，维护全局 Intent 计划，复核 Worker 结果并触发任务完成判定。",
  "vulnerability-validator": "针对单条漏洞假设与基线对照验证，返回证据候选、被推翻的前提与复现步骤。",
  "web-network-analyst": "以最小化且经策略批准的请求分析 Web 或网络 Intent，返回可复现的候选论断。",
};

/** The System Prompt 模板 rendered as numbered Chinese steps. */
const SOLVER_PROMPT_STEPS: Record<string, string[]> = {
  "binary-analysis": [
    "在私有工作区内对样本执行受限的静态与结构分析，不得越出授权范围。",
    "记录元数据、字符串与文件结构，所有派生产物均留存为 Artifact。",
    "输出可复现的观察结论、候选论断、覆盖范围与明确的局限说明。",
  ],
  "code-audit": [
    "定位与当前 Intent 相关的源码入口点，并沿数据流向下追踪。",
    "对每条可疑路径保留可复现的代码位置与证据引用。",
    "输出候选论断，并标注未覆盖的模块与判断依据。",
  ],
  "evidence-reviewer": [
    "逐条比对候选证据与已归档产物，确认其可复现性。",
    "驳回缺乏产物支撑的结论，并写明驳回理由。",
    "记录知识冲突的消解过程与最终采信的版本。",
  ],
  "forensics-analysis": [
    "先保全原始证据并记录哈希或来源引用，再开始分析。",
    "围绕当前 Intent 构建受限的事件时间线。",
    "输出候选知识与威胁指标，并标明取证局限。",
  ],
  "recon-triage": [
    "在授权范围内执行受限侦察，优先使用被动手段。",
    "汇总已覆盖的攻击面、产生的产物与未覆盖部分。",
    "给出后续 Intent 的优先级建议。",
  ],
  "security-reporter": [
    "仅采用已验证的任务知识与已确认的证据论断。",
    "按章节组织结论，并逐条附上证据引用。",
    "在报告结尾说明覆盖范围与未能验证的部分。",
  ],
  "task-supervisor": [
    "维护全局 Intent 计划，按依赖关系与优先级调度 Solver。",
    "复核 Worker 返回的结果，驳回证据不足的结论。",
    "在完成条件满足时触发任务完成判定并归档。",
  ],
  "vulnerability-validator": [
    "针对单条漏洞假设建立基线，再执行对照验证。",
    "记录成功复现的步骤，以及被推翻的前提。",
    "输出证据候选，不做超出验证结果的推断。",
  ],
  "web-network-analyst": [
    "以最小化且经策略批准的请求分析目标 Web 或网络面。",
    "记录请求与响应证据，确保结论可复现。",
    "输出候选论断，并标注受策略限制未能探测的部分。",
  ],
};

/** Reference 05's Team Explorer prints a short role name, not the definition id. */
const SOLVER_SHORT_NAMES: Record<string, string> = {
  "task-supervisor": "Supervisor",
  "recon-triage": "Recon Triage",
  "web-network-analyst": "Web Recon",
  "code-audit": "Code Audit",
  "binary-analysis": "Binary Analyst",
  "forensics-analysis": "IR Analyst",
  "vulnerability-validator": "Validator",
  "evidence-reviewer": "Reviewer",
  "security-reporter": "Reporter",
};

export function solverShortName(definitionId: string, fallback: string): string {
  return SOLVER_SHORT_NAMES[definitionId] ?? fallback;
}

export function solverDescription(id: string, fallback: string): string {
  return SOLVER_DESCRIPTIONS[id] ?? fallback;
}

export function solverPromptSteps(id: string, fallback: string[]): string[] {
  return SOLVER_PROMPT_STEPS[id] ?? fallback;
}

/** Reference 12 lists Skills under business names rather than registry ids. */
const SKILL_LABELS: Record<string, string> = {
  "binary-triage": "二进制样本分诊",
  "code-audit": "源码审计与污点追踪",
  "crypto-and-encoding": "密码与编码还原",
  "evidence-led-incident-response": "证据驱动应急响应",
  "web-recon": "Web 目录与文件枚举",
  "web-vuln-triage": "Web 漏洞检测与验证",
};

const SKILL_SUMMARIES: Record<string, string> = {
  "binary-triage": "用于工作区内已授权的二进制或取证样本：先读取元数据、字符串与文件结构，把派生产物留存为 Artifact；静态证据用尽后，先记录已检查的内容再切换到动态或逆向假设。",
  "code-audit": "用于已授权的本地源码树或赛题附件：从入口点出发追踪数据流与危险汇聚点，保留可复现的代码位置，并在缺乏证据时明确停止。",
  "crypto-and-encoding": "用于观察到的编码数据、密文或确定性变换：先识别编码特征再尝试还原，记录每一步的判断依据与失败路径。",
  "evidence-led-incident-response": "先保全原始证据并记录哈希或来源引用再开始分析；优先构建可复现的时间线，从证据出发归纳威胁指标，而不是从结论倒推。",
  "web-recon": "在尝试利用之前使用：通过字典、robots.txt 和日志线索，发现目标 Web 目录、潜在文件与敏感信息，为后续渗透测试和漏洞挖掘提供依据。",
  "web-vuln-triage": "仅在已观察到的路由、参数、表单或响应行为支持某项假设之后使用：按风险从低到高验证，并保留可复现的请求与响应证据。",
};

export function skillLabel(name: string): string {
  return SKILL_LABELS[name] ?? name;
}

export function skillSummary(name: string, fallback: string): string {
  return SKILL_SUMMARIES[name] ?? fallback;
}

/**
 * Reference 12's left rail groups Skills into seven business categories rather
 * than listing every raw tag.  Each registry tag maps into one of them.
 */
export const SKILL_CATEGORIES = [
  "通用技能", "信息收集", "漏洞分析", "利用与验证", "取证与响应", "报告与总结", "逆向工程",
] as const;

export type SkillCategory = (typeof SKILL_CATEGORIES)[number];

const TAG_CATEGORIES: Record<string, SkillCategory> = {
  recon: "信息收集", links: "信息收集", forms: "信息收集", js: "信息收集", metadata: "信息收集",
  sqli: "漏洞分析", idor: "漏洞分析", taint: "漏洞分析", secrets: "漏洞分析", source: "漏洞分析",
  upload: "利用与验证", auth: "利用与验证",
  "incident-response": "取证与响应", timeline: "取证与响应", ioc: "取证与响应",
  forensics: "取证与响应", evidence: "取证与响应",
  binary: "逆向工程", strings: "逆向工程", crypto: "逆向工程",
  encoding: "逆向工程", decoding: "逆向工程",
};

/** The category a Skill belongs to, from its first tag that maps to one. */
export function skillCategory(tags: readonly string[]): SkillCategory {
  for (const tag of tags) {
    const category = TAG_CATEGORIES[tag];
    if (category) return category;
  }
  return "通用技能";
}
