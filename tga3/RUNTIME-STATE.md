# 工作区内的 .tga3

这是项目运行时的私有状态目录，不是解题结果目录，也不是知识库。
CLI 自己的完整会话历史在 `.codex/` 或 `.claude/`；`.tga3` 仅保存项目需要的控制、恢复和提交状态。

| 文件或目录 | 用途 |
|---|---|
| `codex_cli-session.json` / `claude_cli-session.json` | 保存原生 CLI 会话 ID，续轮或恢复时找到原会话 |
| `blackboard-ack.json` | 记录收到的黑板投递 ID 和 CLI 工作周期，下一检查点确认已阅读 |
| `aoe-start-token` | 判断本次认领的初始黑板是否已经送达；只是随机标记，不是平台凭证 |
| `action-hooks/` | 临时关联工具执行前后的 action 和文件状态；正常结束后移除，意外中断时补记结果 |
| `finding-outbox/` | 系统导入 Finding 的恢复记录，处理发布成功但回执丢失等重试情况 |
| `publication-receipts/` | 保存文件导入状态和生成的黑板条目 ID，供自动去重和会话恢复 |
| Scout 的 request/result 文件 | 驱动采集或普通三轮工作，防止误读旧请求结果 |

正常解题不要求 Worker 遍历这些文件。系统自动注入必要的 action ID、黑板更新和导入回执。
需要恢复丢失的提交回执时，才按需读取 `publication-receipts/`。

执行原始正文在 `result/`；Finding 在独立挂载的 `/findings/`；Worker 的最终候选提交文件在自己的工作区根部。
最终候选验证通过后，系统在题目根目录生成正式 `final-candidate.json`，与 `question.json` 并列。
