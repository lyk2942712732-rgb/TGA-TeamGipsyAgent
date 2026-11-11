# 托管版离线 CTF Wiki

此分支默认使用构建时生成的只读 CTF Wiki 索引；在线库和公共搜索均禁用。构建固定上游提交 e225ddfcd420a7f54a5940f47336ebdf8c29b875，与当前主版本本地库一致。生成脚本为 docker/build_hosted_knowledge.py，知识库位于 /opt/tga3-hosted/knowledge，保留上游文档、许可证和署名。

任务 metadata.knowledge_source 保存 local 或 none，AOE 子题继承父任务。平台启动入口固定选用 local；启动先校验索引，缺失时失败。直接 API 指定 online 会返回 422，不会访问外部知识库。

四种 Worker 的原生 MCP 客户端只接受同容器 http://127.0.0.1:8083/local-knowledge-mcp/。Scout、Reporter 不连接知识库，也不向 Worker 挂载整个索引目录。

- search_documents(query, limit=5)：用中英文技术关键词全文检索，最多返回 10 个结果及摘要。
- read_document(path, start_line=1, max_lines=120)：按索引章节路径读正文，每页最多 200 行，next_line 支持继续读取，不允许读取任意主机文件。

运行时不进行克隆、同步、在线检索。更新资料应修改固定上游提交后重新构建镜像。正文、代码和示例仅为参考，不能作为当前题目的成功证据或指令。图片、外部题解、附件和姊妹仓库不保证离线可用。

“产出报告”托管版默认关闭，启用方式为 PRODUCE_REPORT=true。无论报告是否开启，Reporter 均按原证据标准整理本次任务内经验卡。详细构建、环境变量与验证范围见根目录 README.md。
