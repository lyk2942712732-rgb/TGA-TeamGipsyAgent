# AOE 平台提交与最后 30 秒收尾

AOE 最终候选必须包含准确 flag，并有同题、同 flag 的平台接受回执。普通非 AOE 任务的结果类型不变。平台正确回执是执行日志里的实际响应，不是模型在 rationale 中声称“提交成功”。

## 提交信息

Scout 在题目登记时填写可选的结构化 submission 契约：

```json
{
  "submission": {
    "request": {
      "method": "POST",
      "url": "https://example.invalid/openapi/v1/challenges/submit",
      "headers": {"BENCHMARK_TOKEN": "用户实际提供的凭证"},
      "params": {},
      "body": {"unique_code": "g-05", "flag": "{flag}"}
    },
    "success_path": "correct",
    "duplicate_code": "duplicate"
  }
}
```

`{flag}` 是明确的运行时占位符。URL、方法、参数和成功条件必须来自平台说明，不能猜测。契约随题目 question.json 保存，不另外写一套数据库凭证记录。兼容旧 TSec 记录时，只使用已经明确记录的完整 submit URL 和同源生命周期凭证，不从域名拼接未知接口。

认领时系统把该题提交信息写入 Worker 的 `/workspace/.tga3/submission.json`，避免凭证只出现在可能截断的黑板中。该文件属于本题工作目录，不能发给外部知识库或网页搜索。

## 最终候选

```json
{
  "conclusion": "HTB{exact_flag}",
  "rationale": "实际执行和平台接受回执支持此答案",
  "finding_ids": ["本题已导入的 Finding UUID"],
  "submission_action_id": "worker-openai-action-实际提交动作ID"
}
```

submission_action_id 可显式提供；未提供时系统尝试从本题 Worker 日志自动关联。证据必须来自已结束的原生命令，命令明确包含平台 URL、原始题号和准确 flag，结果正文包含平台成功 JSON。纯 Read/Write、其他题、其他 flag、其他 URL 的回执不能通过。用变量或脚本隐藏了请求绑定信息、仅打印一段声称成功的文字等，不能自动判为已验证，需要按提示重新执行可绑定的提交。

系统不在这一步代发 HTTP 请求，也不主动补交历史答案。

- `submission_required`：缺少可绑定的回执，不能导入最终候选；系统重新写入该题提交接口/凭证文件并把固定路径定向返回 Worker。Worker 读取、提交、保留实际响应后重试。
- `submission_rejected`：平台明确判错或尚未完成全部 flag；不能导入，不能把此错误当成缺凭证反复重发凭证。
- `imported`：同题、同 flag 的接受证据通过，之后才能进入 solved 和报告流程。

平台明确声明 duplicate 表示“相同 flag 已正确提交”时，可将匹配提交请求的 duplicate 回执视作已接受；不能用无关题目/无关 flag 的 duplicate 蒙混通过。若响应提供 correct_flag_count 和 total_flag_count，两者必须相等。

## 最后 30 秒

业务预算仍从首条 action 起算。AOE Worker 不再另叠加一个从 CLI 启动算的固定总时限；保留原生无进展 watchdog。

距离当前尝试期限不超过 30 秒时，系统只检查该尝试最近 20 条原生记录的已完成正文，包括命令输出和工作目录中生成文件的 Read。忽略旧尝试、Write、资料/卡片/Skill/日志读取、命令本身已有的 flag 字面量及明显占位符。flag 形状只是提交收尾信号，不证明答案正确。

发现信号后，保持同一尝试、同一容器/会话，以 120 秒滚动期限继续收尾。直到候选带有效回执发布成功，或用户停止任务。共同攻坚的同组成员共享此延长期限。状态持久化在尝试记录中，重启可以恢复；没有信号时按原预算换题/放弃。

检测只在截止前窗口运行，按日志版本缓存结果；常规动作不反复扫描全部历史日志。Hook 对新结果中的 flag 信号只唤醒后端核实，不能自行授予延期。
