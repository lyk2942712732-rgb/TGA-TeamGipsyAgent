---
name: challenge-classification
modes: [ctf]
capabilities: []
tags: [ctf, classification]
version: 2.0.0
---
# CTF 题型分类

## 目标
识别 Web、Pwn、Reverse、Crypto、Misc 或 Forensics subtype，并给出可审计依据。

## 执行流程
1. 检查题目描述、入口 URL、附件扩展名、Magic、架构与协议线索。
2. 形成 subtype 候选及置信度，必要时提出一个低成本判别动作。
3. 只推荐主力 Solver，不展开完整解题。

## 输出契约
- 主 subtype、备选 subtype 与置信度。
- 分类依据和附件摘要。
- 推荐 Solver definition_id。

## 边界与证据规则
- 分类器不负责拿 Flag。
- 信息不足时保持 unknown/auto。
- 不得因分类扩大工具权限。
