---
name: decryption-script-reconstruction
modes: [reverse_engineering]
capabilities: [input.read, kali.exec, artifact.inspect]
tags: [decrypt, script, reverse]
version: 2.0.0
---
# 解密脚本重建

## 目标
从目标实现重建等价的解密、解码或密钥派生脚本。

## 执行流程
1. 恢复常量、状态、轮函数、模式和字节序。
2. 用已知输入输出或目标运行验证。
3. 保存脚本与测试向量。

## 输出契约
- 可运行脚本。
- 测试向量。
- 适用版本。

## 边界与证据规则
- 不得把近似输出当等价实现。
