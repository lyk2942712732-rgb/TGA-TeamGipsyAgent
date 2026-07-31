---
name: auth-boundary-analysis
modes: [penetration_test, vulnerability_research]
capabilities: []
tags: [auth, authorization, boundary]
version: 2.0.0
---
# 认证授权边界分析

## 目标
识别身份建立、权限检查、对象所有权和信任边界。

## 执行流程
1. 追踪登录、令牌、会话和角色传播。
2. 定位服务端授权决策点。
3. 建立允许/拒绝基线。

## 输出契约
- 边界模型。
- 潜在缺失检查。
- 验证前置条件。

## 边界与证据规则
- 不得使用未授权凭证。
- 客户端限制不等于服务端授权。
