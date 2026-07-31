---
name: reverse-report-review
modes: [reverse_engineering]
capabilities: [artifact.inspect]
tags: [review, report, reverse]
version: 2.0.0
---
# 逆向报告审核

## 目标
审核恢复结果是否回答用户问题且有足够静态/动态证据。

## 执行流程
1. 检查关键主张与代码位置。
2. 验证脚本、配置和协议输出。
3. 标记推断、未覆盖和版本限制。

## 输出契约
- 审核结论。
- 修订项。
- 可交付报告建议。

## 边界与证据规则
- 不以反编译输出数量衡量完成度。
