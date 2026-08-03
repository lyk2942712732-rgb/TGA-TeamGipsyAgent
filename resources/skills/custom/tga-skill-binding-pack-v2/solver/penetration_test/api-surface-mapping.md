---
name: api-surface-mapping
modes: [penetration_test]
capabilities: [kali.exec, artifact.inspect]
tags: [api, recon, pentest]
version: 2.0.0
---
# API 攻击面映射

## 目标
发现 API 端点、方法、认证、参数、对象标识和版本。

## 执行流程
1. 分析文档、流量、前端代码和错误响应。
2. 建立端点与身份上下文矩阵。
3. 标记敏感操作和对象访问边界。

## 输出契约
- API 清单。
- 认证与参数矩阵。
- 验证候选。

## 边界与证据规则
- 发现端点不是授权验证。
- 凭证按最小权限处理。
