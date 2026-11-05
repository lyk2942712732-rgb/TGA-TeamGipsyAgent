# 文件提交与系统导入

Worker 使用原生文件/命令工具写 JSON，不再配置或提供 Finding/最终候选的黑板写入 MCP 工具。
文件保存后，运行时 Hook 通知系统校验并导入，CLI 收到紧凑导入回执。
系统仍保留证据路径校验、任务与执行者绑定、发布幂等和最终候选新鲜度检查。

```text
data/tasks/<批次ID>/<题目ID>/
├── question.json             系统维护的题目信息、附件索引、状态与尝试历史
├── inputs/                   原始附件
├── workspaces/<agent-id>/
│   └── result/final-candidate.json
├── findings/<agent-id>/       在该 Worker 容器内挂载为 /findings
│   └── <agent-id>-finding-001.json
├── logs/<agent-id>/
├── intels/
└── writeups/
```

普通单题任务使用 `data/tasks/<任务ID>/`，同样有 `findings/<agent-id>/`。
每个 Finding 是独立 JSON，导入后保持不变；新事实或修正创建新文件。

例如 `/findings/worker-openai-finding-001.json`：

```json
{
  "action_id": "worker-openai-action-实际ID",
  "claim": "匿名 GET /api/export 返回 403",
  "scope": "仅测试该 GET 请求；登录后的行为未知",
  "preconditions": "没有登录 Cookie"
}
```

`preconditions` 可省略。系统从 action 取得命令、执行状态、正文和附件，自动登记证据并写入黑板。
Worker 不填写 task_id、actor、证据 UUID、复现方法或重试键。
失败文件保留，错误回执说明需要修正的字段。重复处理相同内容不会产生新 Finding。
不同 Worker 的文件目录分别挂载，身份由运行时目录绑定。

成功回执包含：

```json
{
  "path": "/findings/worker-openai-finding-001.json",
  "status": "imported",
  "entry_id": "系统生成的黑板UUID",
  "action_id": "worker-openai-action-实际ID"
}
```

最终候选写到 `/workspace/result/final-candidate.json`：

```json
{
  "conclusion": "实际验证得到的答案",
  "rationale": "该答案得到哪些验证事实支持",
  "answer_type": "flag",
  "finding_ids": ["导入回执中的entry_id"]
}
```

系统先导入 Finding，再检查黑板未读信息，最后导入最终候选。
`waiting_for_blackboard` 表示先阅读系统注入的新消息，下一次原生工具调用或周期结束确认后自动重试同一文件。
只有 `status=imported` 表示成功，`status=error` 需要修正。
导入回执保存在私有工作区 `.tga3/publication-receipts/`，可在恢复会话时检查。

AOE 每次认领先记录 claimed_at；started_at/deadline 在第一条实际 action 时才赋值。
被初始黑板投递阻止的工具不计作实际 action。
共同攻坚由两位 Worker 中第一条实际 action 开始共享预算；之后的动作不延长预算。
启动时间不扣解题预算，CLI 的超时与无进展恢复机制仍单独生效。

黑板通过每题、每 Worker 的游标增量投递。初次认领/新会话提供初始信息，恢复同一会话的普通续轮不重放完整快照。
未确认送达的消息可重放；实时用户指令和最终候选检查会越过普通投递间隔。
Hook 的原生命令仅运行标准库客户端，处理和 HTTP 连接池常驻在 Worker 进程中，跨 CLI 工作周期复用。
