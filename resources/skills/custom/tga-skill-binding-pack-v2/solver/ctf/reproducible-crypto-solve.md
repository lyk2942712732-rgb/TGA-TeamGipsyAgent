---
name: reproducible-crypto-solve
modes: [ctf]
capabilities: [workspace.read, workspace.write, workspace.python, artifact.inspect]
tags: [crypto, solve, ctf]
version: 2.0.0
---
# 可复现密码求解

## 目标
产出可重复运行的密码求解脚本和包含 Flag 的 Artifact。

## 执行流程
1. 固定输入格式和依赖。
2. 验证解码、填充、字节序与 Flag 格式。
3. 保存脚本、输出和关键中间值。

## 输出契约
- 求解脚本。
- 运行命令。
- 候选 Flag Artifact。

## 边界与证据规则
- 手工抄录结果不算可复现。
- 不得泄露与任务无关的秘密。
