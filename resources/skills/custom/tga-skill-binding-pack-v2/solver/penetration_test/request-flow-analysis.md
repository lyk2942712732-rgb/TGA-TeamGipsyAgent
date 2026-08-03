---
name: request-flow-analysis
modes: [penetration_test]
capabilities: [kali.exec, artifact.inspect]
tags: [request, data-flow, web]
version: 2.0.0
---
# 请求与数据流分析

## 目标
追踪参数从客户端到服务端组件、存储和下游请求的路径。

## 执行流程
1. 记录输入点、编码、验证和输出点。
2. 识别跨服务、模板、数据库或 URL 处理。
3. 选择最低影响验证方式。

## 输出契约
- 输入到敏感操作的数据流。
- 编码与过滤点。
- 验证候选。

## 边界与证据规则
- 不得根据参数名猜漏洞。
- 需要响应或代码证据。
