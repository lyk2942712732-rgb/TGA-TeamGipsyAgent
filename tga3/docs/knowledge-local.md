# 按任务选择在线 / 本地知识库

新建任务的「Worker 知识库」可选：

- **在线知识库 · ctf42**：默认选项，保留原有技法、技术栈、历史题和 WP 链接检索。
- **本地知识库 · CTF Wiki**：使用 `https://github.com/ctf-wiki/ctf-wiki` 的本地 Markdown 索引。中英文关键词检索、正文分页读取不请求外部服务，不需要模型调用、向量数据库或额外容器。

选择保存于任务 `metadata.knowledge_source`（`online` / `local`）；普通任务和 AOE 子题启动均按此选择配置四种 Worker 原生 MCP。AOE 子题继承父任务设置，恢复时从存储读取。历史任务缺省为 `online`。Scout / Reporter 不连接知识库。运行图显示当前任务使用的知识库，并识别两类检索事件。选择本地库不改变独立的公共 Web 搜索功能。

## 准备与更新

在后端主机的 `tga3` 目录，以控制服务可读取的用户运行（需要 Git、首次下载需要联网、Python SQLite 需要 FTS5）：

```sh
python -m tga3.cli sync-knowledge
```

首次克隆上游的 Markdown 与许可证，生成 SQLite FTS5 索引。已存在目录时只重建索引；主动拉取更新并重建使用：

```sh
python -m tga3.cli sync-knowledge --update
```

自定义配置目录可放在命令前：`tga3 --config-dir /path/to/config sync-knowledge`。更新只允许 Git fast-forward，遇到本地修改或冲突会失败，不覆盖本地修改。索引先写临时文件，成功后替换旧文件；失败保留旧索引。同步完成后无需重启服务即可读取新索引。

`runtime.json` 支持以下可选字段，路径相对于 `tga3` 项目目录：

```json
{
  "ctf_wiki_root": "knowledge/ctf-wiki",
  "ctf_wiki_index": "knowledge/ctf-wiki.sqlite3"
}
```

资料和索引位于 Git 忽略目录，不提交上游全文，也不打入 Worker 构建上下文。部署至另一台主机时需在该主机执行同步命令。后端、前端更新后，四种 Worker 镜像也需要重新构建以包含新的 MCP 名称与提示词。

`GET /api/v3/knowledge-sources` 返回索引是否可用、篇数和上游版本。索引未就绪时界面禁用本地选项，直接 API 创建本地任务也返回 422；不会静默切换到在线库。在线状态仅表示已配置，不代表外部网络探活成功。

## 本地调用路径

```text
Worker 原生 MCP 客户端（ctf_wiki）
  -> 现有控制服务 /local-knowledge-mcp/
  -> 本地只读 SQLite 索引
```

- `search_documents(query, limit=5)`：最多 10 个结果，返回章节路径、标题、摘要和摘要起始行。英文词和中文双字词索引，多个查询词共同匹配，按全文相关性排序。
- `read_document(path, start_line=1, max_lines=120)`：只接受索引里的章节路径，每页最多 200 行，返回 `next_line` 供继续读取。通过数据库读取，不允许任意主机文件路径。

保留上游仓库许可证。下载范围是 Markdown 正文与许可证；图片、外链 WP、附件和姊妹仓库没有保证离线可用。正文和示例是参考数据，Worker 仍需针对当前题目验证条件、发布实际执行证据。
