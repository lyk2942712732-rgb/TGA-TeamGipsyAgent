# TGA · 本地 CTF Wiki 托管版

分支 codex/tsecbench-hosted-local-knowledge。普通主版本使用 codex/scout-two-stage；不允许知识库的平台使用 codex/tsecbench-hosted。

此变体在镜像中附带只读离线 CTF Wiki，保留四种 Worker（Codex、Claude、OpenCode、Qwen）、Scout、Reporter、AOE 调度、逐题黑板、Finding、动作日志和 Flag 提交保护。单个平台容器内使用独立进程、工作目录、CLI Home 和会话，不依赖 Docker socket 或嵌套 Docker；平台容器是隔离边界。

## 离线知识库

构建阶段从 https://github.com/ctf-wiki/ctf-wiki 获取固定提交 e225ddfcd420a7f54a5940f47336ebdf8c29b875，与现有本地知识库版本一致，并生成 SQLite 全文索引。保留上游文档、许可证和署名资料，移除 Git 历史。运行时不下载、不更新，也不访问在线知识库或公网搜索。

四个 Worker 只连接同容器回环地址上的 ctf_wiki MCP，使用 search_documents 搜索、read_document 分页读正文。Scout 和 Reporter 不连接知识库。文档作为参考，方法仍需在当前题目验证。启动会校验索引，缺失时明确失败。镜像不包含个人配置、密钥、历史评测题目、答案、报告、经验卡、会话或个人 Skills；本次评测内生成的经验卡仍可跨题复用。

## 构建与运行

此分支尚未构建或导出镜像 tar。历史 hosted-20261110 Release 是旧的无知识库版本，不能作为本变体下载使用。

先按原流程准备共享工具基础镜像和四种 CLI 镜像，然后执行：

    cd tga3
    bash scripts/build-hosted.sh tga3-hosted-local-knowledge:local
    docker save -o tga3-hosted-local-knowledge.tar tga3-hosted-local-knowledge:local
    sha256sum tga3-hosted-local-knowledge.tar

构建需要访问上游 GitHub；运行时无需公网。Dockerfile 的上下文允许列表只打包运行代码、干净模板及构建脚本，知识库由固定上游提交生成。基础环境还需 git 和支持 FTS5 的 Python SQLite（原工具基础镜像已提供）。

平台注入 BENCHMARK_BASE_URL、BENCHMARK_TOKEN。模型变量参考 tga3/hosted.env.example：Codex（含 Scout/Reporter）使用 CODEX_* 和 Responses 协议；Claude 使用 CLAUDE_* 和 Anthropic 协议；OpenCode/Qwen 默认共用 CLAUDE_*，可通过各自前缀和 PROTOCOL 覆盖。模型域名自动添加 .tsecbench.gw 并转换为 HTTP；平台和靶机接口保持原地址。支持的模型由平台网关决定。

    docker run --rm --network none --env-file hosted.env -e BENCHMARK_TOKEN=configuration-check-only -e BENCHMARK_BASE_URL=http://platform.internal tga3-hosted-local-knowledge:local --check-config

正式运行使用镜像默认入口，自动启动私有 PostgreSQL、控制服务及 AOE。首次/第二次/共同攻坚预算由 AOE_FIRST_MINUTES / AOE_SECOND_MINUTES / AOE_JOINT_MINUTES 设置，默认 30/15/20 分钟；BENCHMARK_INSTANCE_QUOTA 默认 3。收到停止信号会回收 CLI 子进程并关闭数据库。

## 报告与提交保护

“产出报告”默认不勾选；PRODUCE_REPORT=false 时 Reporter 按原证据要求只提炼经验卡，不生成或保存 Markdown 报告。设为 true 启用现有完整报告与经验卡流程。界面中的知识库选择和“产出报告”位于附件后的“执行与产出”区域。

每次 Worker 收到明确答案错误回执，系统最多补交一次当前尝试证据中的精确 Flag，使用整理出的题目提交接口与凭证，记录 action 并将补交回执反馈给原 Worker。保留成功回执校验和最终候选发布流程。

## 验证范围

本次仅验证源码和单元测试，包括四种 CLI 的离线 MCP 配置、索引检索/API、报告选项、提交保护及 AOE 调度。按要求未在 Windows 或 Ubuntu 构建托管镜像，也未进行真实平台评测。上传平台前需自行构建、导出并检查镜像大小及平台知识库规则。

详细说明见 tga3/EXPERIENCE.md、tga3/FILE-PUBLICATIONS.md 和 LICENSE。
