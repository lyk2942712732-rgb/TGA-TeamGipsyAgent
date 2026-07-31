---
name: protocol-and-format-recovery
modes: [reverse_engineering]
capabilities: [workspace.read, artifact.inspect]
tags: [protocol, format, reverse]
version: 2.0.0
---
# 协议与文件格式恢复

## 目标
恢复消息、文件、序列化和状态机格式。

## 执行流程
1. 识别 Magic、字段、长度、校验和和版本。
2. 追踪编码解码函数。
3. 生成解析器或构造器验证。

## 输出契约
- 格式规范。
- 解析脚本。
- 样本验证。

## 边界与证据规则
- 推断字段需标注置信度。
