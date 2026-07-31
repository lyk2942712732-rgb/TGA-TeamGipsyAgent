---
name: network-enumeration
modes: [penetration_test]
capabilities: []
tags: [network, recon, pentest]
version: 2.0.0
---
# 网络枚举

## 目标
在范围与强度允许下识别主机、端口、服务和版本。

## 执行流程
1. 核对 CIDR、排除项和速率。
2. 先被动或低影响探测，再按需要深化。
3. 将结果映射到资产与服务清单。

## 输出契约
- 主机/端口/服务矩阵。
- 版本和证据。
- 覆盖缺口。

## 边界与证据规则
- 不宣称漏洞。
- 禁止超范围扫描。
