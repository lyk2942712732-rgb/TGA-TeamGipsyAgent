---
name: steg-and-file-analysis
modes: [ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [forensics, steg, ctf]
version: 2.0.0
---
# 隐写与文件分析

## 目标
从媒体、文件结构、元数据、附加数据和编码层中恢复隐藏内容。

## 执行流程
1. 检查格式一致性、通道、块、元数据和尾部数据。
2. 根据证据应用有限的提取与解码。
3. 保存每一层派生 Artifact。

## 输出契约
- 提取链。
- 派生文件与 Hash。
- 候选 Flag Artifact。

## 边界与证据规则
- 不要盲目运行不可信提取脚本。
- 每层转换必须可追溯。
