# 项目工具与原生文件提交

项目工具会按角色写进运行时提示词，完整源头是 `workers/prompts.py` 与 `workers/aoe_scout.py`。

| 角色/工作内容 | 项目 MCP 工具 |
|---|---|
| AOE Scout 收集 | `scout_register_questions(questions=[...])`、`scout_finish_collection(expected_count)` |
| 普通 Scout 提取 | `scout_write_intel(action_id, claim, detail?, topic?, evidence_paths?)` |
| 普通 Scout 合并 | `scout_merge_intel(merged_from_ids, claim?, detail?, topic?)` |
| Worker 本地知识 | `find_technique`、`find_atom`、`find_wp`、`search`、`read_topic`、`get_payload`、`run_script`、`solve_hint` |
| Worker 公开网络资料 | `web_search(query, max_results?)` |

Worker 当前共九个项目 MCP 工具。`search` 搜索本地知识库；`web_search` 搜索公开网络资料。
Codex 的原生 `web_search` 也显式设为 live；Claude 的原生 WebSearch/WebFetch 已在白名单中开放。
当前供应商的 Claude 原生 WebSearch 实测不可用，提示词优先引导它使用项目 MCP `web_search`。
项目搜索复用已验证可用的 Responses 搜索后端，不新增搜索容器、不要求另一份 API 密钥。

Finding 不需要 MCP 工具：Codex 使用原生 `exec_command`/`apply_patch`，Claude 使用原生 `Write`/`Bash` 写文件。
执行后 Hook 会给出真实 action_id。Worker 将该 ID、claim、scope 和可选 preconditions 写到 `/findings/<agent>-<id>.json`。
系统随后完成证据登记、写入黑板和导入回执。最终候选同样使用原生文件提交，见 FILE-PUBLICATIONS.md。

提示词明确要求：查公开文档、既有技术与题解来寻找思路；不在查询中携带任务凭证；参考资料不能直接当作当前目标的验证证据。
连续两次搜索失败后停止重复搜索和改词重试，继续本地分析。项目搜索也会在同一尝试连续失败两次后阻止新的上游请求。
