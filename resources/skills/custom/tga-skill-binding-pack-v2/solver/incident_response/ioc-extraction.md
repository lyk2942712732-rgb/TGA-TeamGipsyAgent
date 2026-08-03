---
name: ioc-extraction
modes: [incident_response]
capabilities: [input.read, artifact.inspect]
tags: [ioc, incident]
version: 2.0.0
---
# IOC 提取

## 目标
从证据中提取并规范化可操作的 IOC，同时保留上下文和置信度。

## 执行流程
1. 提取域名、IP、URL、Hash、路径、账号和注册表等指标。
2. 去重并关联首次/末次出现。
3. 区分恶意、可疑和环境正常值。

## 输出契约
- 结构化 IOC 表。
- 上下文与来源。
- 置信度和误报说明。

## 边界与证据规则
- 孤立字符串不能自动标记恶意。
- 敏感内部指标按交付策略处理。
