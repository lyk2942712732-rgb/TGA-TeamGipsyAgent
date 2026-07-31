---
name: memory-forensics
modes: [incident_response]
capabilities: [workspace.read, artifact.inspect]
tags: [memory, forensics]
version: 2.0.0
---
# 内存取证

## 目标
从内存镜像恢复进程、模块、网络、凭证痕迹和注入迹象。

## 执行流程
1. 确认镜像类型和 OS/符号。
2. 枚举进程、模块、句柄、网络和可疑内存区。
3. 导出派生 Artifact 供进一步分析。

## 输出契约
- 内存发现。
- 进程与网络关系。
- 导出对象和限制。

## 边界与证据规则
- 不得暴露无关凭证。
- 插件失败需记录。
