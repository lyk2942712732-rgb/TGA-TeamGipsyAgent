---
name: input-type-classification
modes: [incident_response, ctf]
capabilities: [workspace.read, artifact.inspect]
tags: [input, classification]
version: 2.0.0
---
# 输入类型分类

## 目标
根据 Magic、结构、字段和内容识别真实数据类型。

## 执行流程
1. 检查扩展名与 Magic 是否一致。
2. 抽样解析头部、时间和关键字段。
3. 选择安全解析器与资源预算。

## 输出契约
- 真实类型。
- 解析器建议。
- 异常或伪装标志。

## 边界与证据规则
- 不要自动执行文件。
