---
name: ctf-reverse-analysis
modes: [ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [reverse, ctf, solve]
version: 2.0.0
---
# CTF 逆向求解

## 目标
恢复题目核心算法并生成可复现求解脚本。

## 执行流程
1. 重建控制流与数据变换。
2. 用样本或模拟验证等价性。
3. 生成求解脚本并保存输出 Artifact。

## 输出契约
- 算法说明。
- 求解脚本。
- 候选 Flag Artifact。

## 边界与证据规则
- 脚本输出必须可重复。
- 不执行超出策略的样本行为。
