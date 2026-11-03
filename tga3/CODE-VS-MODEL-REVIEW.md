# 可以由代码接管的确定性工作

本次按当前三轮 Scout、原生 CLI Worker、黑板 MCP 和 Reporter 实现审查。
以下是后续建议，除明确列为“本次已处理”的内容外，没有扩大实现范围。

## 本次已处理

- Intel 身份与重试：服务端生成稳定 ID，分别使用 `scout-raw-intel-`、`scout-merged-intel-` 前缀，模型不再取 key 名称。
- Intel 文件组织：服务端原子追加两份阶段汇总文件，模型不拼接或维护 JSON 文件。
- Intel 证据登记：原始阶段只校验路径；合并来源、证据路径并集及最终登记交给代码。
- Finding 主题：不保存 topic；界面需要时从 claim 前 200 个字符派生。
- 删除没有实际使用的 `blackboard_sync` MCP 工具和 `blackboard_publish` 的旧 Finding 分支。
  原生 Hook 仍通过内部黑板服务同步；最终候选仍使用 `blackboard_publish`。

## 剩余机会

| 优先级 | 现在由模型处理的机械工作 | 更合适的代码处理 | 留给模型的判断 | 依据 |
|---|---|---|---|---|
| 高 | Scout 第二轮收到未覆盖 action ID 后，自行读取 logs 并定位、读取每份正文和附件 | 代码按缺失 ID 组装命令、状态、正文摘要和附件索引；大文件保留按需读取，避免一次灌入全部输出 | 提炼可复用事实、描述负面测试边界 | `workers/openai_worker.py: scout/uncovered_actions`，`workers/prompts.py` 第二轮规则 |
| 高 | Scout 写原始 Intel 时仍重复填写 task_id、source_action_ids 和证据路径 | MCP 连接绑定任务；默认从当前 action 关联正文和附件，模型只提供事实及必要的证据选择 | 哪段内容支持哪项事实、是否需要缩小证据范围 | `ScoutIntelInput`、`mcp_server.py: scout_write_intel` |
| 高 | Worker 提交最终候选时仍复制 task_id、actor、生成 idempotency_key | 像 finding_publish 一样从运行时连接绑定身份；按规范化请求生成重试键 | 最终结论、论证与支持它的 Finding ID | `domain.py: WorkerPublicationBase`、`workers/prompts.py: final_candidate_example` |
| 中 | 第三轮对只含一条原始 Intel 的组，也要求模型重新抄写 claim/detail/topic | 支持直接引用原始 ID，服务端复制该条正文；多条合并才需要模型提供新正文 | 决定分组、整理多条互补信息 | `ScoutIntelMergeInput`、`workers/openai_worker.py: unmerged_intels` |
| 中 | 完全文字相同的原始 Intel 仍进入模型的合并判断 | 对相同 topic/claim/detail 做精确分组，代码保留全部来源并集；不要把相似句或不同条件当作相同事实 | 语义近似、条件差异、先失败后成功的修正关系 | `scout_intel.py: merge`，当前去重 ID 还包括来源，因此相同文字但不同来源会保留为不同条目 |
| 中 | Reporter 根据黑板和证据重新整理命令、路径、执行者等固定信息 | 代码生成不可改写的复现命令块和证据索引，模型引用这些块并撰写解释 | 因果链、关键步骤选择、结论与局限 | `host_agents.py: Automation._report/CliHostModel.report`，Reporter 原生工具读取证据 |

## 已经由代码完成，不需要再让模型做一遍

action ID 分配、执行返回正文留存、附件发现、第二/三轮暂停 journaling、覆盖检查、
合并来源去重、证据路径校验、证据元数据登记、Finding 身份绑定与重试去重、
黑板增量投递与确认、Token 汇总、容器清理，均已有代码实现。

不建议把“什么值得发布为 Finding”“某项失败能排除多大范围”“两条情报是否语义相同”
替换成只依赖退出码、关键词或字符串相似度的规则。这些仍需要理解当前任务、输入条件和结果。
也不建议增加 Worker 定期审阅 logs 的阶段。

若继续优化，先考虑为 Scout 自动提供逐 action 的紧凑材料，以及最终候选的身份和重试键自动绑定。
这两项减少模型机械填写与读取，且不改变它对事实和结论的判断职责。
