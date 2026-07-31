/**
 * Reference image 05's Intent Board contents.
 *
 * The board pads each column with these rows when the live task has not filled
 * it, so the workspace keeps the shape the design specifies instead of showing
 * five empty columns.  Sample rows carry no intent id: they cannot be selected
 * and never reach the inspector.  See `pages/sample.ts` for the same contract on
 * the catalog pages.
 */

export type IntentCardView = {
  key: string;
  /** Real rows carry the intent id; sample rows carry null. */
  intentId: string | null;
  title: string;
  objective: string;
  status: string;
  priority: string;
  solver: string | null;
  /** Column-specific detail lines — 预计 / 运行时长 / 耗时 / 阻塞原因 … */
  metrics: Array<[string, string]>;
  percent: number | null;
  flag: "approval" | "blocked" | "done" | null;
  sample: boolean;
};

function sample(
  column: string, title: string, objective: string, status: string, priority: string,
  solver: string | null, metrics: Array<[string, string]>, percent: number | null,
  flag: IntentCardView["flag"],
): IntentCardView {
  return { key: `${column}:${title}`, intentId: null, title, objective, status, priority, solver, metrics, percent, flag, sample: true };
}

/** Cards per column, plus the reference's total so the footer count matches. */
export const SAMPLE_INTENTS: Record<string, { total: number; cards: IntentCardView[] }> = {
  pending: {
    total: 6,
    cards: [
      sample("pending", "收集子域名", "枚举目标主域下的可解析子域并去重", "pending", "中", null, [["预计", "15 分钟"], ["创建", "10:05"]], null, null),
      sample("pending", "识别科技栈", "识别 Web 指纹、框架版本与中间件", "pending", "中", null, [["预计", "20 分钟"], ["创建", "10:06"]], null, null),
      sample("pending", "端口扫描", "在授权网段内探测开放端口与服务", "pending", "高", null, [["预计", "30 分钟"], ["创建", "10:07"]], null, null),
    ],
  },
  running: {
    total: 8,
    cards: [
      sample("running", "目录枚举", "使用字典发现隐藏目录与备份文件", "running", "高", "Web Recon", [["运行时长", "18 分钟"]], 60, null),
      sample("running", "参数抓取", "抓取请求参数并归纳可控输入点", "running", "中", "Web Recon", [["运行时长", "12 分钟"]], 40, null),
      sample("running", "敏感信息泄露检测", "检索响应体与源码中的凭据与密钥", "running", "高", "Code Audit", [["运行时长", "22 分钟"]], 65, null),
    ],
  },
  awaiting_approval: {
    total: 3,
    cards: [
      sample("approval", "SQL 注入验证", "对候选参数执行受控注入验证", "awaiting_approval", "高", "Code Audit", [["创建", "11:35"]], null, "approval"),
      sample("approval", "文件上传漏洞验证", "验证上传点的类型校验与落地路径", "awaiting_approval", "高", "Code Audit", [["创建", "11:50"]], null, "approval"),
      sample("approval", "权限提升路径验证", "验证越权访问链路是否可复现", "awaiting_approval", "中", "Web Recon", [["创建", "11:55"]], null, "approval"),
    ],
  },
  completed: {
    total: 12,
    cards: [
      sample("completed", "Web 服务识别", "识别 Web 服务类型与版本", "completed", "低", null, [["耗时", "8 分钟"], ["完成", "10:18"]], null, "done"),
      sample("completed", "SSL/TLS 信息收集", "收集证书链、协议与加密套件", "completed", "低", null, [["耗时", "6 分钟"], ["完成", "10:22"]], null, "done"),
      sample("completed", "robots.txt 分析", "解析 robots.txt 中的暴露路径", "completed", "低", null, [["耗时", "4 分钟"], ["完成", "10:27"]], null, "done"),
    ],
  },
  blocked: {
    total: 2,
    cards: [
      sample("blocked", "内网访问探测", "探测内网可达性与横向路径", "blocked", "高", null, [["阻塞原因", "网络限制"], ["阻塞时间", "11:20"]], null, "blocked"),
      sample("blocked", "API 接口暴力枚举", "枚举未公开的 API 路由", "blocked", "高", null, [["阻塞原因", "速率限制"], ["阻塞时间", "11:45"]], null, "blocked"),
    ],
  },
};
