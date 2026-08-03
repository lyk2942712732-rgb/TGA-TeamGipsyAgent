---
name: injection-and-ssrf-validation
modes: [penetration_test]
capabilities: [kali.exec, artifact.inspect]
tags: [injection, sqli, ssrf, validation]
version: 2.0.0
---
# 注入与 SSRF 验证

## 目标
用最小影响方式验证 SQL/命令/模板注入或服务端请求假设。

## 执行流程
1. 确认输入到敏感解释器或 URL 请求的路径。
2. 优先使用无害、可识别的差异或回连机制。
3. 记录前置条件、响应和影响边界。

## 输出契约
- 最小 Payload。
- 请求响应 Artifact。
- 影响与未证实部分。

## 边界与证据规则
- 禁止破坏性命令和数据修改。
- SSRF 目标仍受网络政策。
