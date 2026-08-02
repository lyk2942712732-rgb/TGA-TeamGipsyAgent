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

/**
 * A Solver Definition id rendered as a short role name, derived from the id
 * itself rather than a hand-maintained table: `ctf-web-solver` reads as
 * "Ctf Web".  Definitions the registry adds therefore need no frontend change.
 */
export function solverShortName(definitionId: string, fallback: string): string {
  const words = definitionId
    .split(/[-_]/)
    .filter((part) => part && part !== "solver" && part !== "definition");
  if (!words.length) return fallback;
  return words.map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ");
}

/**
 * Skill display names and summaries come from the registry's own Markdown, which
 * is already authored in Chinese: the first heading is the title and the body is
 * the summary.  Nothing is authored here, so an imported Skill reads the same way
 * as a packaged one.
 */
export function skillLabel(name: string, summary?: string): string {
  const heading = summary?.match(/^#\s+(.+?)(?:\s+##|$)/)?.[1]?.trim();
  return heading || name;
}

export function skillSummary(_name: string, fallback: string): string {
  return fallback;
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
