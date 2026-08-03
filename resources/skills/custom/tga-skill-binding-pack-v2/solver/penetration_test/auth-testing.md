---
name: auth-testing
modes: [penetration_test]
capabilities: [kali.exec, artifact.inspect]
tags: [auth, validation]
version: 2.0.0
---
# 认证机制验证

## 目标
在授权范围内验证登录、会话、令牌、恢复和多因素机制。

## 执行流程
1. 建立正常与异常基线。
2. 测试明确允许的绕过、重放、固定或生命周期假设。
3. 保存最小复现与影响。

## 输出契约
- 验证结果。
- 复现请求/响应。
- 影响与限制。

## 边界与证据规则
- 避免账号锁定和大规模猜测。
- 遵守凭证与速率政策。
