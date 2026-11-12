# TGA · TSec 托管镜像

此分支为 `codex/tsecbench-hosted`，独立于日常 Ubuntu 部署分支。

镜像启动后读取平台注入的 `BENCHMARK_TOKEN`、`BENCHMARK_BASE_URL`，自动启动私有
PostgreSQL、控制服务及 AOE 任务。保留 Codex、Claude Code、OpenCode、Qwen Code 四个
Worker，以及 Scout、Reporter、逐题黑板、Finding、动作日志、提交回执校验和现有调度。

平台只提供一个容器，本分支用独立进程、工作目录、CLI Home 和会话执行智能体，不依赖
Docker socket、特权模式、嵌套 Docker 或额外网络服务。平台容器是隔离边界，Worker
之间没有各自的内核容器隔离；资料视图和证据路径是本地文件视图。

镜像不包含在线/本地知识库、ctf42 MCP 连接或知识库路由，不提供公网搜索。
构建上下文采用允许列表，只打包项目运行代码、干净配置模板及原有通用工具；不打包
`data/`、历史题目、答案、报告、经验卡、CLI 对话、模型密钥或个人 Skills。
本次运行中新生成的黑板、报告和任务内卡片保存在一次性运行目录中，不读取其他任务的资料。

## 获取镜像

本分支只维护受限托管运行；普通主版本和 Web 控制台使用 `codex/scout-two-stage`。

源码在本分支，`docker save` 导出的 Docker 镜像 `.tar` 包放在对应 GitHub Release 的附件中。
大型镜像不提交普通 Git 文件；校验信息随 Release 提供。

- [下载 Docker 镜像 `.tar` 包](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261110/tga3-hosted-20261110-linux-amd64.tar)
- [下载 SHA-256 校验文件](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/download/hosted-20261110/tga3-hosted-20261110-linux-amd64.tar.sha256)
- [Release 与验证说明](https://github.com/lyk2942712732-rgb/TGA-TeamGipsyAgent/releases/tag/hosted-20261110)

文件大小为 **1,188,388,864 字节（约 1.19 GB）**，平台为 `linux/amd64`，镜像标签为
`tga3-hosted:20261110`。这是 `docker save` 生成的原始 `.tar` 文件，可以直接上传平台，
也可用 `docker load -i tga3-hosted-20261110-linux-amd64.tar` 导入。
SHA-256：`79da0909d067ccc4c6ecefdbcbedfd3a69b23302c8101dc662f045eefa673513`。

普通主版本安装的九类 CTF Skill 包包含历史题解与具体示例，本托管镜像也不包含这些包。
保留 Skill 的读取能力与空目录，不把历史资料以 Skill 名义带入评测。

## 已完成验证

- 在 `--network none` 下运行镜像中实际安装的四种 CLI，无源码或依赖覆盖挂载。
- 合成模型和靶场仅监听容器本地回环地址；Scout 收集四道合成题，四种 Worker 分别执行
  原生工具、发布 Finding、提交答案，收到四份 `correct:true`，生成四份报告并正常退出。
- 共 45 次合成模型调用，知识库工具调用为零；验证了 Responses、Messages、Chat 三种基础协议路径。
- Linux 下的进程隔离目录、环境变量白名单、独立会话子进程清理等 13 项测试通过。

此验证证明断网启动与本地执行链路可用；不代表已经验证平台的真实网关、模型能力或完成正式托管评测。
OpenCode 所需插件依赖已经预装，启动无需访问 npm；模型推理仍需要平台允许的模型网关。

## 平台环境变量

参考 [`tga3/hosted.env.example`](tga3/hosted.env.example)。平台自动注入两项 `BENCHMARK_*`，无需将值写入程序。

| CLI | 必填环境变量 | 供应商必须支持的协议 |
|---|---|---|
| Codex（含 Scout、Reporter） | `CODEX_BASE_URL`、`CODEX_MODEL`、`CODEX_API_KEY` | OpenAI Responses |
| Claude Code | `CLAUDE_BASE_URL`、`CLAUDE_MODEL`、`CLAUDE_API_KEY` | Anthropic Messages |
| OpenCode、Qwen Code | 默认共用 `CLAUDE_*`，可分别用 `OPENCODE_*` / `QWEN_*` 覆盖 | 默认 Anthropic Messages，与当前 Ubuntu 配置一致 |

OpenCode/Qwen 可分别通过 `OPENCODE_PROTOCOL` / `QWEN_PROTOCOL` 选择 `anthropic`、
`openai_responses` 或 `openai_chat_completions`。选择 Chat Completions 时，默认值来自
`CHAT_BASE_URL` / `CHAT_MODEL` / `CHAT_API_KEY`；选择 Responses 时来自 `CODEX_*`。
填写平台白名单内供应商实际支持的模型和协议。启动时自动把模型域名加 `.tsecbench.gw`、
把 HTTPS 改为 HTTP，保留 API 路径；已转换的域名不会重复追加。题单、靶机和提交接口不会
进行模型网关转换。供应商仍须在平台白名单内；网关转换不会使不受支持的供应商变得可用。

首次/第二次/共同攻坚预算由 `AOE_FIRST_MINUTES` / `AOE_SECOND_MINUTES` /
`AOE_JOINT_MINUTES` 设置，默认 30/15/20 分钟；`BENCHMARK_INSTANCE_QUOTA` 默认 3。

可先用 `--check-config` 验证环境变量、协议配置和网关地址，不联网、不调用模型：

```sh
docker run --rm --network none --env-file hosted.env \
  -e BENCHMARK_TOKEN=configuration-check-only \
  -e BENCHMARK_BASE_URL=http://platform.internal \
  tga3-hosted:20261110 --check-config
```

正式运行直接使用镜像默认入口。运行结束时等待逐题报告/卡片收尾后退出；收到停止信号时
停止任务、回收 CLI/工具子进程并关闭私有数据库。`tini` 负责回收孤儿进程。

## 构建与导出

先按项目原构建流程准备共享工具基础镜像和四种固定版本 CLI 镜像，然后：

```sh
cd tga3
bash scripts/build-hosted.sh tga3-hosted:20261110
docker save -o tga3-hosted-20261110.tar tga3-hosted:20261110
sha256sum tga3-hosted-20261110.tar
```

Dockerfile 使用专用 `.dockerignore`，不继承本地 `data` 或配置密钥。只从各 CLI 镜像复制
CLI 安装目录，共用原有 Python/Node/安全工具环境；没有新建 Python 虚拟环境。
新增系统依赖只有私有数据库所需的 PostgreSQL Server 及其系统依赖，以及进程回收器 `tini`；
额外预装 OpenCode 自带插件入口所需的 `@opencode-ai/plugin@1.19.5` 及其依赖、锁文件，
各 OpenCode 会话共用只读安装目录，避免在断网环境启动时等待 npm 下载。
控制服务所需的 FastAPI、asyncpg、uvicorn 等安装进同一个 Python 环境。

上传前以实际导出文件大小核对平台 3GB 上限。平台截图规定的资源为 8 核、16GB 内存、
20GB 总存储。正式托管评测仍需要在平台配置真实的模型参数和密钥；离线验收不代表已经
在平台提交过一次正式评测。

## 任务内经验卡

**保留本次评测内部的跨题卡片。** 成功题目的 Reporter 依据本次执行证据提炼局部解决经验，系统按本次任务归属提供紧凑目录，其他题目的 Worker 遇到对应问题时读取正文/附件。

镜像中不预置此前评测生成的卡片、答案或报告；启动时创建新的运行数据目录。正常结束会等逐题报告和卡片处理收尾。物理卡片存储与任务过滤机制相同，不能把“不预装历史卡片”理解为“关闭卡片功能”。

## 源码与运行数据

| 路径 | 用途 |
|---|---|
| `tga3/src/tga3/hosted.py` | 环境变量配置、私有数据库、控制服务和 AOE 自启动 |
| `tga3/src/tga3/process_runtime.py` | 同一平台容器内各 CLI 的独立进程与私有目录 |
| `tga3/docker/Dockerfile.hosted*` | 托管镜像构建、构建上下文允许列表 |
| `tga3/docker/hosted-opencode-deps/` | OpenCode 断网启动所需插件及依赖锁文件 |
| `tga3/hosted.env.example` | 平台模型变量模板；不含真实密钥 |
| `tga3/config/` | 干净的运行模板；模型密钥与 Skills 不烘焙入镜像 |
| `tga3/src/tga3/experience.py` | 本次任务经验卡提炼、导入与任务过滤 |

`apps/web`、普通 Docker 运行时及其服务样例仍在源码分支中，未打进托管镜像的文件可另行审查；没有为了文件清理擅自删掉运行时代码或测试。

详细的黑板与文件边界见 [运行时说明](tga3/README.md)、[文件发布](tga3/FILE-PUBLICATIONS.md)、[经验卡](tga3/EXPERIENCE.md)。许可证见 [LICENSE](LICENSE)。

待用户决定的旧文件与临时产物见 [文件整理清单](tga3/docs/file-cleanup-review.md)，清单所列候选尚未删除。

## 当前源码更新

此分支不包含知识库，继续禁用外部知识检索。已同步主版本的 Flag 提交保护：每次 Worker 收到明确答案错误回执，最多补交一次当前尝试证据中的精确 Flag，并把补交回执回馈原 Worker。

托管运行默认不产出报告，Reporter 仅整理经证据校验的经验卡。设置 PRODUCE_REPORT=true 可启用原有报告与经验卡流程。前端选项名称为“产出报告”。已有下载的历史 tar 不会随源码更新；需自行重建并导出新镜像。
