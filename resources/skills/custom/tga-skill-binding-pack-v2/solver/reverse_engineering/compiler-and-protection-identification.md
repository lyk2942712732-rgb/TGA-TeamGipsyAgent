---
name: compiler-and-protection-identification
modes: [reverse_engineering]
capabilities: [input.read, artifact.inspect]
tags: [compiler, protection, reverse]
version: 2.0.0
---
# 编译器与保护识别

## 目标
识别编译器、运行时、链接方式、符号、保护和库版本。

## 执行流程
1. 分析文件头、节区、导入、异常信息和特征。
2. 记录 PIE、NX、Canary、CFG/CFI 等保护。
3. 评估对分析方法的影响。

## 输出契约
- 编译画像。
- 保护矩阵。
- 工具建议。

## 边界与证据规则
- 特征匹配需标注置信度。
