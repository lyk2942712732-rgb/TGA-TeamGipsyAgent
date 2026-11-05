# 执行镜像工具基线

基础系统和安全工具清单对齐 StrikeAgent 的 TSecBench 评测 Dockerfile：
https://github.com/Yean-Sec/StrikeAgent_AtkBrain-Flash/blob/7526baa7ea88de04b1900f04a06b13b77ab5682e/Dockerfile

采用 Python 3.12 slim-bookworm，按同一清单安装基础网络、编译、调试、Web、Pwn、Crypto 与常用取证工具。
安全类 pip 清单为 pwntools、pycryptodome、gmpy2、z3-solver、ropper。
Debian 的 python3-* 包供系统 Python/工具使用，Worker 默认的 `python` 是独立的 Python 3.12 venv；不要假定两个 Python 的第三方包完全相同。

沿用对方的可选包策略：必装包失败即构建失败，可选包不可用则跳过。
每项结果保存为镜像内 `/opt/tga3/tool-install-status.tsv`，构建自检会输出它。
不因清单中出现名称就向智能体保证该工具已经安装。

项目保留自己的运行代码、Python 运行依赖、两个 CLI、Node 22.22.0、非 root 用户和挂载/权限机制。
额外保留 ripgrep，支持原生 CLI 文件搜索。不会引入 Pi、StrikeAgent 后端、前端或其 Agent 调度逻辑。
不再额外预装旧基线的 Ghidra/Android、angr、QEMU、专业取证/多媒体和整套 Kali headless 工具组；清单依赖可能带入少量相关运行库。

## 项目独立依赖层

安全工具之外，两个 Worker 镜像都通过 `pip install /opt/tga3` 安装项目本体与 `pyproject.toml` 的统一运行依赖：

| 依赖 | 用途 |
|---|---|
| httpx | 常驻 Hook 的 HTTP 通信、项目 HTTP 请求 |
| websockets | Worker 与控制面的双向控制通道 |
| pydantic | 控制协议、配置及结构化数据校验 |
| pyyaml | 知识库 YAML 读取；验收确认 C 加速 SafeLoader 可用 |
| mcp | 项目 MCP 协议与服务支持 |
| asyncpg | 项目 PostgreSQL 持久化模块 |
| fastapi | 项目控制面 HTTP API |
| python-multipart | 项目附件上传 |
| uvicorn[standard] | 项目 ASGI 服务运行 |

后四项主要供控制面模块使用，目前随项目整包安装。Worker 不直接连接 PostgreSQL。
这些依赖沿用项目现有声明；安装库不等于每次动作都初始化对应模块。Hook 的命令客户端仍只加载标准库。
Python 构建工具 pip/setuptools/wheel、Node 与两个 CLI 也保留在独立层；其他传递依赖由 pip 解析，使用 `pip check` 验收。
宿主机的 Docker SDK 属于 `[control]` 可选依赖，不额外装进 Worker 镜像。

## 构建与验收

```sh
bash scripts/build-images.sh
```

默认沿用 `:local` 标签。可在不覆盖运行标签的情况下完整构建候选：

```sh
TGA3_IMAGE_TAG=bookworm-candidate bash scripts/build-images.sh
```

构建脚本依次构建安全工具层、Node 层、两个 CLI/项目代码层，运行命令/Python/GDB自检、项目模块导入与 CLI 版本检查。
镜像中不写 API 密钥；模型、MCP、知识库与任务挂载仍由项目运行时提供。

可配置 `PYTHON_IMAGE`（仍须为 Python 3.12 slim-bookworm）、`DEBIAN_MIRROR`（主机名）、`NODE_DOWNLOAD_BASE`、`DOCKER_BUILD_NETWORK`。
`DEBIAN_MIRROR=` 保留基础镜像中的官方 Debian 源。默认基础镜像家族和 Debian 镜像站与上游相同。
基础镜像固定为本轮实测的镜像摘要，避免同名 tag 更新导致工具层漂移；升级基础系统时显式覆盖 `PYTHON_IMAGE` 并重新验收。
Node 和两个 CLI 使用项目已验证的固定版本，传输保持 TLS 验证。

基础层的实际变化需要从新 FROM 重新构建；在旧 Kali 镜像上删除文件不能移除旧层。
候选验收通过后再切换 Worker 标签，保留旧镜像用于回滚，不清理数据库卷和任务数据。
