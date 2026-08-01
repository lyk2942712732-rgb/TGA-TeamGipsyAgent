---
name: web-api-analysis
modes: [penetration_test]
capabilities: [http.request, artifact.inspect]
tags: [web, api, analysis]
version: 2.0.0
---
# Web/API 行为分析

## 目标
理解应用流程、状态转换、会话、业务对象和输入传播。

## 执行流程
1. 重建关键用户流程。
2. 比较不同角色、状态和对象的响应。
3. 把差异转为可验证安全假设。

## 输出契约
- 流程图。
- 身份/对象差异。
- 验证 Intent 建议。

## 边界与证据规则
- 不得直接改变生产数据。
- 业务差异不自动等于漏洞。
