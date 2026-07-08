# TGA LLM 配置说明

当前 Week1 MVP 已提供 OpenAI-compatible 模型适配层，方便接入国内大模型或比赛指定 AI 安全网关。

## 环境变量

```bash
TGA_LLM_BASE_URL=https://你的模型网关/v1
TGA_LLM_API_KEY=你的密钥
TGA_LLM_MODEL=你的模型名
```

例如比赛要求通过指定 AI 安全网关接入时，通常把 `TGA_LLM_BASE_URL` 配成网关给出的 OpenAI-compatible 地址。

## 连通性检查

```bash
python scripts/tga_llm_healthcheck.py
```

未配置时会返回 `LLM_NOT_CONFIGURED`，这不是错误，只表示当前仍在使用规则型 MVP planner。

## 当前限制

LLM 适配层已经存在，但还没有完全接管任务规划。下一步建议把 `tga/orchestrator/planner.py` 从固定规则升级为：

1. 规则 planner 生成安全边界和最低限度步骤；
2. LLM planner 根据题目描述、工具目录、历史证据生成候选计划；
3. Scope Gate 和 Evidence Gate 审核后再执行；
4. 所有模型建议只作为 lead，不能直接确认 flag 或漏洞。
