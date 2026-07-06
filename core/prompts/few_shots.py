"""A deliberately small successful CTF trajectory."""

FEW_SHOT = """\
## 简短示例

黑板：GET 参数 id 已确认；假设 id 存在 SQL 注入。
助手：
[PATH] SQL 注入 | 验证 id 是否影响后端查询 | 发送单引号并比较响应
[HYPOTHESIS] id 参数未经安全处理 | 请求 id=1'
随后调用 http_get。

工具返回 SQL 语法错误后，Core 将真实响应写入已确认事实。助手再基于该证据推进，
而不是重复同一个请求。若工具最终返回 flag{example}，助手仅输出：
[FLAG_CANDIDATE] flag{example}
等待 Core 比对与用户手动提交。
"""
