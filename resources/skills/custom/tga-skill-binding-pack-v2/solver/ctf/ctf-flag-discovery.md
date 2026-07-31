---
name: ctf-flag-discovery
modes: [ctf]
capabilities: [artifact.inspect]
tags: [ctf, flag, artifact]
version: 2.0.0
---
# CTF Flag 发现

## 目标
从工具输出、页面、文件、脚本或解码结果中识别候选 Flag 并保留原始证据。

## 执行流程
1. 按 Task flag_format 搜索候选值。
2. 保存包含完整候选值的任务 Artifact。
3. 提交候选值、Artifact ID、定位和获取步骤。

## 输出契约
- 候选 Flag。
- Artifact 与定位。
- 复现步骤。

## 边界与证据规则
- 不得只在自然语言回复中给出 Flag。
- 不接受示例或占位 Flag。
