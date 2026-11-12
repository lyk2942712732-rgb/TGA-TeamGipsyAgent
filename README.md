# TGA · 无知识库托管版

分支 codex/tsecbench-hosted。这是无前端的单容器托管版本，已从此分支移除 apps/web、Nginx 配置和依赖前端的普通部署脚本。普通 Web 控制台及 Ubuntu 部署使用 codex/scout-two-stage。

镜像自动启动私有 PostgreSQL、内部控制服务和 AOE，保留四种 Worker（Codex、Claude Code、OpenCode、Qwen Code）、Scout、Reporter、逐题黑板、Finding、动作日志、Flag 提交保护与现有调度。无需浏览器界面、端口映射、Docker socket、特权模式或嵌套 Docker。各智能体使用独立进程、工作目录、CLI Home 和会话；平台容器是隔离边界。

## 下载与加载

- [镜像 tar](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261112/tga3-hosted-20261112-linux-amd64.tar)
- [环境变量模板 hosted.env.example](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261112/hosted.env.example)
- [SHA256SUMS](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261112/SHA256SUMS)
- [构建与验证信息 release.json](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261112/release.json)
- [本次 Release](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/tag/hosted-20261112)

这是 linux/amd64 的原始 docker save tar，可直接上传托管平台，或导入本地 Docker：

    sha256sum -c SHA256SUMS
    docker load -i tga3-hosted-20261112-linux-amd64.tar
    cp hosted.env.example hosted.env
    # 编辑 hosted.env，替换供应商地址、模型和 API key
    docker run --rm --network none --env-file hosted.env -e BENCHMARK_TOKEN=configuration-check-only -e BENCHMARK_BASE_URL=http://platform.internal tga3-hosted:20261112 --check-config

--check-config 只验证配置，不联网、不调用模型。正式托管使用默认入口，平台注入 BENCHMARK_BASE_URL 和 BENCHMARK_TOKEN。自行运行需传入真实平台地址和令牌，并允许访问平台及模型网关。模板不含真实密钥，密钥在平台运行环境填写，不提交 Git。旧 hosted-20261110 Release 与本次镜像不同。

## 环境变量

完整模板见 [tga3/hosted.env.example](tga3/hosted.env.example)，同一文件随 Release 附带。

| 用途 | 环境变量 | 协议 |
|---|---|---|
| Codex、Scout、Reporter | CODEX_BASE_URL / CODEX_MODEL / CODEX_API_KEY | OpenAI Responses |
| Claude Code | CLAUDE_BASE_URL / CLAUDE_MODEL / CLAUDE_API_KEY | Anthropic Messages |
| OpenCode、Qwen Code | 默认共用 CLAUDE_*，可分别用 OPENCODE_* / QWEN_* 覆盖 | 默认 Anthropic Messages |

OpenCode/Qwen 可通过各自的 PROTOCOL 选择 anthropic、openai_responses 或 openai_chat_completions。Responses 默认读取 CODEX_*；Chat Completions 默认读取 CHAT_BASE_URL / CHAT_MODEL / CHAT_API_KEY。供应商及协议必须在平台网关允许范围内。模型 URL 自动追加 .tsecbench.gw，HTTPS 转 HTTP，保留路径；平台与题目接口不做此转换。

AOE_FIRST_MINUTES / AOE_SECOND_MINUTES / AOE_JOINT_MINUTES 默认 30/15/20 分钟，BENCHMARK_INSTANCE_QUOTA 默认 3。

“产出报告”通过 PRODUCE_REPORT 配置，默认 false：Reporter 仍按现有证据标准整理经验卡，可读取 Finding、动作日志等证据，不生成 Markdown 报告；true 启用完整报告及经验卡流程。正常结束等待收尾，停止信号回收 CLI 子进程并关闭数据库。

每次 Worker 收到明确答案错误回执，提交保护最多补交一次当前尝试证据中的精确 Flag，使用整理出的题目提交接口与凭证，记录 action 并将补交回执反馈原 Worker，保留最终候选及成功回执校验流程。

## 知识与运行数据

镜像不包含在线/本地知识库、ctf42 MCP，也不提供公网搜索。需要离线 CTF Wiki 时使用 codex/tsecbench-hosted-local-knowledge 分支的独立镜像。

镜像不包含个人配置、模型密钥、历史评测题目、答案、报告、经验卡、CLI 对话或个人 Skills。本次评测中生成的经验卡仍可在同一任务内跨题复用，运行资料保存在新的运行目录。Dockerfile 使用构建上下文允许列表，主机数据不会随构建进入镜像。

## 从源码构建与导出

先准备共享工具基础镜像和四种固定版本 CLI 镜像（见 tga3/scripts/build-images.sh），然后：

    cd tga3
    bash scripts/build-hosted.sh tga3-hosted:20261112
    bash scripts/export-hosted.sh tga3-hosted:20261112 ../release

导出脚本生成 tar、env 模板和校验文件。大型 tar 放在对应分支提交的 GitHub Release 附件中；Git 保留源码、模板、下载链接及 tga3/releases/hosted-20261112.json。构建需要访问 apt/PyPI/npm，正式运行只使用平台允许的网络服务。

## 验证范围

实际镜像 ID、文件大小、校验值、源码提交和验证结果见 release.json。离线配置检查与合成平台验收不代表真实模型能力或已完成正式平台评测。

黑板、文件发布与经验卡机制见 [运行说明](tga3/README.md)、[文件发布](tga3/FILE-PUBLICATIONS.md)、[经验卡](tga3/EXPERIENCE.md)。许可证见 [LICENSE](LICENSE)。
