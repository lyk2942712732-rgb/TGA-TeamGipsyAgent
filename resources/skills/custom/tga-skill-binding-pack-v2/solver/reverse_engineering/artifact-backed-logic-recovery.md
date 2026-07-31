---
name: artifact-backed-logic-recovery
modes: [reverse_engineering]
capabilities: [artifact.inspect]
tags: [reverse, recovery, evidence]
version: 2.0.0
---
# Artifact 支持的逻辑恢复

## 目标
把恢复出的算法、配置、协议和脚本绑定到反汇编、反编译或运行 Artifact。

## 执行流程
1. 标记关键函数、数据结构和控制流位置。
2. 用交叉引用、样本输入或动态轨迹验证解释。
3. 输出可复现的解密、解析或配置提取脚本。

## 输出契约
- 恢复结果。
- 代码位置与 Artifact 引用。
- 复现脚本和限制。

## 边界与证据规则
- 字符串猜测不能替代逻辑证据。
- 不得把未验证 C2、密钥或行为写成事实。
