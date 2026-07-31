---
name: incident-investigation-planning
modes: [incident_response]
capabilities: []
tags: [incident, planning, triage]
version: 2.0.0
---
# 事件调查规划

## 目标
根据用户调查问题和输入类型建立 Triage、Timeline、Forensics、Malware 与报告 Intent。

## 执行流程
1. 先完成证据分类。
2. 按数据类型选择 Host、Network、Memory 或 Cloud Forensics。
3. 仅在出现可疑样本时创建 Malware Solver。

## 输出契约
- 调查问题到 Intent 的映射。
- 数据源覆盖矩阵。
- 按需 Solver 激活条件。

## 边界与证据规则
- 避免在证据不足时做归因。
- 优先非破坏性分析。
