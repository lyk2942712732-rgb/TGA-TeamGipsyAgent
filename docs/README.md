# TGA 文档索引

| 目录 | 内容 |
| --- | --- |
| [architecture/](architecture/) | 目标架构、领域术语、依赖规则、安全与恢复模型，以及 Phase 02–11 的分阶段设计记录 |
| [guides/](guides/) | 使用配置指南：Provider 与模型（LLM_CONFIG）、MCP 配置、测试运行（TESTING） |
| [operations/](operations/) | 运维手册（TGA_RUNBOOK）、沙箱搭建、阿里云部署、发布门禁清单（CUTOVER_CHECKLIST） |
| [releases/](releases/) | 版本发布说明与迁移指引（schema v6、V2 迁移） |
| [performance/](performance/) | 性能基线与原始基准数据 |
| [assets/](assets/) | 文档配图（架构图 SVG/PNG） |
| [archive/](archive/) | 历史材料，仅供追溯，不代表当前架构 |

## 入口文档

- 项目总览与快速开始：[../README.md](../README.md)
- 当前架构：[architecture/TARGET_ARCHITECTURE.md](architecture/TARGET_ARCHITECTURE.md)
- 领域术语：[architecture/DOMAIN_GLOSSARY.md](architecture/DOMAIN_GLOSSARY.md)
- 分层依赖约束：[architecture/DEPENDENCY_RULES.md](architecture/DEPENDENCY_RULES.md)

## 相关目录

生成物（报告、幻灯片、迁移试运行证据）不在 `docs/` 下，位于仓库根部的 `artifacts/`。
运行期数据写入 `runs/`，该目录不纳入版本控制。
