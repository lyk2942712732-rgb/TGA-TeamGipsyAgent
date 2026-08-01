---
name: reverse-static-triage
modes: [ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [reverse, ctf, triage]
version: 2.0.0
---
# CTF 逆向静态分诊

## 目标
快速定位 Flag 检查、编码、状态机、反调试和关键函数。

## 执行流程
1. 检查入口、字符串、导入和交叉引用。
2. 定位输入到比较或解密路径。
3. 提取最小关键函数集合。

## 输出契约
- 关键函数与地址。
- 输入变换路径。
- 动态或脚本验证建议。

## 边界与证据规则
- 不得从字符串直接猜 Flag。
- 反编译解释需交叉验证。
