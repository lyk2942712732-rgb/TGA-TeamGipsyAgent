# 四 Worker 执行

默认执行者为 `worker-openai`（Codex CLI）、`worker-claude`（Claude Code CLI）、`worker-opencode`（OpenCode CLI 1.19.5）、`worker-qwen`（Qwen Code CLI 0.27.0）。Scout 和 Reporter 的职责不变。

两种新 CLI 均支持项目现有的 Anthropic、OpenAI Chat Completions 和 Responses 端点，可在 Solver、新建任务、运行中的模型控制面板独立选择。API Key 通过环境提供；OpenCode 的配置仅存环境变量引用。模型切换在原有周期边界执行，保留原会话。

## AOE 调度

1. 收集阶段继续只派发简单题；全批无难度标记时按现有规则认领。四个空闲 Worker 可以并行领取四道不同的题目，任务锁和原子占用防止重复认领。
2. 每题最多两次独立尝试，由不同 Worker 执行。继续优先派发没有尝试过的题目，再按来源明确给出的难度排序。首次、第二次预算保持现有配置，从各次首条动作开始。
3. 保留整批阶段切换规则：全部题目已收集、剩余题目均完成两次独立尝试且无人仍在独立解题后，逐题共同攻坚。**该题的另外两位、未独立尝试过的 Worker** 接手；前两位无需同时空闲或恢复运行。
4. 攻坚两位使用各自的新会话，先读取本题黑板和公共原文，按需读取前两位的只读证据。`/evidence-logs/<agent>-actions.json` 的正文/附件路径相对于 `/evidence-files/<agent>/`，可复制相关脚本到自己的工作区修改。仅挂载本题的结果和已记录附件，CLI 会话状态仍在原 Worker 私有目录。
5. 共同攻坚两位共享一份预算，从第一条实际动作同时开始计时。中断恢复继续原攻坚会话和已有截止时间；超时放弃该题。完成一题仅结束该题及其报告，其他题继续。

历史任务沿用它创建时已有的 Worker 记录。原两 Worker 任务继续使用原攻坚人员规则，历史记录和截止时间无需迁移。

## CLI 与数据

- 复用已有 Debian/slim 工具基础镜像和 Python 环境；新镜像仅增加各自 CLI 包及项目代码。没有另装 Python 或增加控制服务。
- 两种 CLI 都调用现有常驻 HookService。OpenCode 使用原生插件的工具前后事件，Qwen 使用原生命令钩子。日志仍每位一个 `<agent>-actions.json`，原始正文仍在各自 `workspace/result/`。
- OpenCode 插件在模型消费增量前暂缓相应动作，不把暂缓记成一次执行；提交回执不会反复阻止下一条提交命令。并行动作、工具失败、中断和恢复均保留独立 action。
- `.opencode`、`.qwen` 和 `.tga3` 内部文件不参与输出附件扫描。两种 CLI 的实际 Token 回执以紧凑用量记录供现有统计模块增量读取、按原生消息/步骤 ID 去重。
- Finding、最终候选、平台成功回执校验、30 秒收尾保护、任务内经验卡和 Skills 沿用现有机制。卡片仍先提供完整紧凑目录，解题过程中按需读取。
- 知识库与项目 `web_search` MCP 均接入。Qwen 默认延迟展示 MCP 工具 schema，需用 `tool_search` 获取实际工具名/参数，再通过 `tool_call` 调用；这沿用 CLI 的省上下文机制。提示词明确说明了此入口。

运行图读取任务实际 Worker 列表：四位左右各两位，右侧向下错开；旧两位任务保留双节点。阶段线和知识库线覆盖四位，活动线绕开节点。窄屏按列堆叠；模型信息、详情、战绩与折叠仍可使用。

原生接口参考：[OpenCode 1.19.5 插件接口](https://github.com/anomalyco/opencode/blob/v1.19.5/packages/plugin/src/index.ts)、[Qwen Code 0.27.0 钩子](https://github.com/QwenLM/qwen-code/blob/v0.27.0/packages/core/src/hooks/types.ts)、[Qwen 工具延迟发现](https://github.com/QwenLM/qwen-code/blob/v0.27.0/packages/core/src/tools/tool-search.ts)。
