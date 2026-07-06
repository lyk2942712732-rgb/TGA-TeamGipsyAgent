# Agent Core MVP

`core` implements the stateful reasoning loop for the CTF agent. Platform and
security tools are supplied by the other project modules; Core only schedules
them and records their evidence.

Design documents:

- [Agent Core 接口与协作文档](AGENT_CORE_INTERFACES.md)
- [Agent Core 详细设计文档](AGENT_CORE_DETAILED_DESIGN.md)

## Public API

```python
from core import CoreConfig, create_ctf_agent

agent = create_ctf_agent(
    model=model,
    tools=[*mcp_tools, *security_tools],
    config=CoreConfig(),
)

result = await agent.start(
    "Solve the Web CTF challenge",
    thread_id="challenge-001",
    challenge_id="web-001",
    target="http://target/",
)

# After Core has returned an evidence-verified Flag:
result = await agent.resume("提交成功", thread_id="challenge-001")
```

The same `thread_id` must be used for every turn. The default in-memory
checkpointer is intended for MVP development; production callers should pass a
durable LangGraph checkpointer.

## Blackboard rules

- Confirmed facts are created only from real `ToolMessage` results.
- LLM ideas remain hypotheses until a tool verifies them.
- Identical tool arguments producing the same error three times are blocked on
  later attempts.
- `submit_flag` is hidden from the model even when included in the supplied
  tool list.
- A Flag is first extracted from tool evidence, then repeated by the LLM and
  compared exactly. It becomes a confirmed fact only after the user reports a
  successful manual submission in the same thread.

## Required dependencies

Core was verified with:

```text
langgraph 1.2.7
langchain-core 1.4.8
pydantic 2.13.4
```

Dependency ownership remains with the infrastructure role; this package does
not modify the repository-level dependency file.
