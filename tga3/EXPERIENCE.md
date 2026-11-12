# 任务内问题经验卡

经验卡用于解决执行中遇到的一个具体问题、报错或可复用子问题，不是整道题的战略方案。
仅在来源题目成功后，由 Reporter 在原报告会话内顺带提炼零至三张卡；系统检查格式、
真实 action 引用和资源路径后入库。未解出或放弃的题目不提炼。卡片有问题不改变解题结果，
导入错误记录在文件状态中，任务运行图点击“经验卡”可查看。

```text
data/experience/
├── cards/exp-<id>.json
├── assets/exp-<id>/<可复用文件>
├── index.json                  # 可重建的系统索引
└── state.json                  # 制作状态与去重指纹
```

卡片字段固定为 `card_id`、`title`、`applies: {signals, requires}`、`steps`、`verify`、
`limits`、`resources`、`sources: [{task_id, question_id, action_ids, validation}]`。
资源路径相对于 `data/experience`。编号和所属任务/题目由系统绑定；`validation` 只描述
原执行证据支持的程度，不能把成功结算或模型判断冒充平台判题接受。

`sources.task_id` 在 AOE 中是父任务 ID，所有来源必须属于同一任务。API、资源下载和
Worker 只读视图都按所属任务隔离。删除所属任务会清理它的卡片与资源。经验功能不向
PostgreSQL 增加内容、索引、状态或使用事件，也不添加 MCP 工具、独立提炼 Agent 或检索模型。

## Worker 如何使用

Worker 只读挂载 `/experience/`，其中有当前任务全部卡片的 `index.json`、`cards/` 和 `assets/`。
开始时原生 Hook 提供简短目录（名称、信号、路径），不根据题面自动选择方法，不注入卡片全文。
目录超过注入长度时明确给出完整索引路径。遇到具体障碍时，Worker 用 CLI 原生 Grep/Read 或
shell 搜索索引、读取对应卡片，再核对 `applies.requires`，验证当前题目中的解决办法。

新卡产生后，只在自然工具边界告知新增目录项，不打断命令、不强制开启新模型轮次。
投递回执保存在本题 Worker 工作区 `.tga3/experience-delivery.json`，按 CLI 上下文而非
每次命令/工作周期去重。恢复同一 CLI 上下文保留目录已读位置，新上下文重新给出目录。
Worker 读取卡片仍遵循正常 action 日志记录，但读取经验不是新方法有效的执行证明。

## Reporter 输出

同一次报告整理中写 `/workspace/experience-cards.json`：

```json
{
  "cards": [{
    "title": "ZIP 解析器无法读取图片附件时提取尾部归档",
    "applies": {"signals": ["ZIP parser error", "trailing_data"], "requires": ["尾部数据确为有效归档"]},
    "steps": ["解析图片结构定位结束位置", "验证并提取附加归档后解包"],
    "verify": ["归档目录可以列出且能提取文件"],
    "limits": ["该次记录未验证加密归档"],
    "resources": ["experience-assets/extract.py"],
    "sources": [{"action_ids": ["<真实 action_id>"], "validation": "reproduced"}]
  }]
}
```

原题地址、凭证、答案和固定临时路径不应进入方法；可复用脚本先参数化后复制到自己的
`experience-assets/`。无可复用方法可明确写 `{"cards":[]}`。文件/引用格式错误最多在原
CLI 会话内修正一次，不开启额外模型审核流程；仍失败时保留错误和原报告。重复成功通知和
报告重试使用稳定卡片 ID 去重。文件是权威来源，索引损坏可由卡片重建，历史卡片不会在
服务启动时自动重新调用模型提炼。
# 报告开关

新建任务的“产出报告”托管版默认不勾选。勾选时保持现有 Reporter 报告与经验卡流程；取消勾选时，Reporter 仍可按需读取完整执行证据并校验经验卡引用，但只整理经验卡，不生成或保存 Markdown 报告。AOE 各子题继承任务创建时的选择。未解出的题目仍不会提炼成功经验。

