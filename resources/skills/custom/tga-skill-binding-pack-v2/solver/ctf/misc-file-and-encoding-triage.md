---
name: misc-file-and-encoding-triage
modes: [ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [misc, encoding, file]
version: 2.0.0
---
# Misc 文件与编码分诊

## 目标
识别文件嵌套、编码链、压缩、媒体、二维码、协议和自动化线索。

## 执行流程
1. 检查 Magic、元数据、尾部数据和嵌套容器。
2. 枚举有限的编码或转换候选。
3. 按证据选择解包、解码或脚本路线。

## 输出契约
- 文件层级。
- 编码链候选。
- 下一步处理。

## 边界与证据规则
- 避免无界爆破编码组合。
- 所有转换保留输入输出 Hash。
