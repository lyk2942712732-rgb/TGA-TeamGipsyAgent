---
name: crypto-primitive-identification
modes: [ctf]
capabilities: [input.read, artifact.inspect]
tags: [crypto, classification]
version: 2.0.0
---
# 密码原语识别

## 目标
识别编码、古典密码、哈希、对称、RSA、ECC、格或自定义构造及其参数。

## 执行流程
1. 整理已知量、未知量和数学关系。
2. 检查参数规模、随机数复用、泄漏和实现偏差。
3. 给出可行攻击候选及复杂度。

## 输出契约
- 原语与参数。
- 攻击候选。
- 所需额外信息。

## 边界与证据规则
- 不要把编码误判为加密。
- 保留精确大整数和字节序。
