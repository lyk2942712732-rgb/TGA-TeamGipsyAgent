---
name: packing-and-obfuscation-triage
modes: [reverse_engineering]
capabilities: [input.read, artifact.inspect]
tags: [packing, obfuscation, reverse]
version: 2.0.0
---
# 壳与混淆分诊

## 目标
识别压缩、壳、虚拟化、控制流平坦化和字符串混淆。

## 执行流程
1. 检查节区熵、入口、导入和异常结构。
2. 区分打包与恶意行为。
3. 选择安全解包或静态绕过路线。

## 输出契约
- 混淆类型。
- 证据。
- 后续路线。

## 边界与证据规则
- 不得直接运行未知解包器。
