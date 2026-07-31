export type BackendCapabilityState = "available" | "read_only" | "unsupported";

export type BackendCapability = {
  state: BackendCapabilityState;
  reason: string;
};

export const BACKEND_CAPABILITIES = {
  confirmedResults: { state: "unsupported", reason: "尚未提供最近确认结果独立接口" },
  reportCatalog: { state: "read_only", reason: "报告列表、查看与导出可用；编辑和版本管理尚未提供" },
  knowledgeCatalog: { state: "read_only", reason: "知识库目录可读取；管理、同步和检索测试接口尚未提供" },
  teamTemplates: { state: "read_only", reason: "团队模板是内置只读资源" },
  solverDefinitions: { state: "read_only", reason: "Solver Definition 是内置只读资源" },
  policyCatalog: { state: "read_only", reason: "执行策略目录可读取；创建、复制和版本管理尚未提供" },
  capabilityRegistry: { state: "read_only", reason: "Capability 注册表可读取；独立启停和规则编辑尚未提供" },
  modelProviders: { state: "available", reason: "当前后端支持一个 Provider 的读取、保存和验证" },
  multiModelProviders: { state: "unsupported", reason: "尚未提供多 Provider、Profile、Routing 和验证历史接口" },
  systemResources: { state: "unsupported", reason: "尚未提供 CPU、内存和磁盘指标接口" },
} satisfies Record<string, BackendCapability>;

