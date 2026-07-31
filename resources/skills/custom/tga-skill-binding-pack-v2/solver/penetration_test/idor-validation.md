---
name: idor-validation
modes: [penetration_test]
capabilities: [http.request, artifact.inspect]
tags: [idor, authorization, validation]
version: 2.0.0
---
# 对象授权验证

## 目标
通过受控对象差异验证水平或垂直越权。

## 执行流程
1. 使用两个已授权身份或明确测试对象。
2. 比较允许与拒绝基线。
3. 证明服务端返回或执行了未授权对象操作。

## 输出契约
- 对象和身份矩阵。
- 复现 Artifact。
- 已证实影响。

## 边界与证据规则
- 不得枚举真实用户数据。
- 只使用范围内测试对象。
