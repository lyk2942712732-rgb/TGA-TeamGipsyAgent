# 执行镜像：slim 单一 Python 环境

工具范围参考 StrikeAgent TSecBench Dockerfile：
https://github.com/Yean-Sec/StrikeAgent_AtkBrain-Flash/blob/7526baa7ea88de04b1900f04a06b13b77ab5682e/Dockerfile

从固定摘要的 Python 3.12 slim-bookworm **重新构建**，不继承旧 TGA/Kali/venv 镜像。
系统工具交给 apt；Python 安全工具和项目运行依赖全部由同一个 Python/pip 管理。

## 唯一的执行环境

- Python 为 `/usr/local/bin/python3`，包统一位于 `/usr/local/lib/python3.12/site-packages`。
- `python`、`python3`、`/usr/bin/python3` 指向同一解释器；不建立 `/opt/tga3-venv`。
- 普通 shell、登录 shell、CLI shell、Hook 和知识库 MCP 都使用该环境。
- `/etc/profile` 保留已传入的 PATH，避免登录 shell 丢掉原生 CLI 的临时工具目录（如 Codex 的 apply_patch 辅助入口）。仅在 PATH 为空时设置系统默认值。
- 公共 site-packages 与 Python 命令目录可由容器内 UID 1000 写入，题内确需 pip 安装依赖时仍装在同一环境，不自动退回另一份 user-site。写入只影响该执行容器，不改基础镜像或其他容器。
- 不通过 apt 安装 Python 应用或 `python3-*` 第三方库，也不把 Debian 3.11 包目录拼接进 3.12 的路径。
- Debian GDB 依赖 `libpython3.11` 及标准库以执行它自己的嵌入式脚本。这是 GDB 的系统运行库，不提供第二个 Python 命令、pip 或安全工具包环境；不将它冒充可以卸载的冗余。
- 不改宿主机服务的 `.venv`，它不属于执行容器。

## 工具安装分工

apt 保留网络、编译、调试、Web、密码和取证工具，包括 nmap/ncat、GCC/GDB、
ffuf/gobuster/dirb、hydra/john、tshark、whatweb、steghide 和各服务客户端。

原来由 Debian Python 安装的 sqlmap、wfuzz、wafw00f、hashID、binwalk、Impacket、
SymPy、Pillow、BeautifulSoup、密码库改由同一 pip 环境提供。
pwntools、ROPgadget、ropper、gmpy2、Z3 与这些库共存；checksec 只保留 pwntools 实现。
Impacket 使用上游的 `smbclient.py` 等入口。
PyCryptodome 的 `Crypto` 与 Impacket 所需 PyCryptodomex 的 `Cryptodome` 是两个命名空间，保留两者是依赖要求。

`ctf-python-requirements.txt` 固定直接依赖，`python-constraints.txt` 固定已验收的传递依赖。
镜像内 `/opt/tga3/python-packages.lock` 记录实际版本。
可选的 nikto/radare2/feroxbuster/enum4linux 仍按可用性安装；若会带入另一个 Python 应用环境则跳过。
结果保存为 `/opt/tga3/tool-install-status.tsv`，不能凭工具清单承诺其已经存在。

旧工具只做必要兼容修补，源归档 SHA256 在 `build-python-tools.py` 中固定：

- wfuzz 3.1.0：修正非法的 `pyparsing>=2.4*` 依赖声明；插件加载器从 imp 改为 importlib。
- binwalk v2.3.4 源码：将已移除的 `imp.load_source` 改为 importlib；该源码的包元数据版本是 2.3.3。
- Capstone 固定稳定的 5.x，保留 binwalk 2.x 使用的 ARM64 API。

不额外安装 Ghidra/Android、angr、QEMU、Sage 或整套 Kali headless。

## 项目特定依赖

两个 Worker 只安装项目核心依赖：HTTPX、WebSockets、Pydantic、PyYAML、MCP。
MCP 自身依赖 uvicorn、python-multipart，仍由解析器安装，不能误删。
asyncpg、FastAPI、`uvicorn[standard]`、Docker SDK 放在 `[control]`，供控制端安装：

```sh
python -m pip install '.[control]'
# 开发/完整测试
python -m pip install -e '.[dev,control]'
```

项目仅以 wheel 安装一份，构建目录、源码副本和 wheel 缓存留在构建阶段，不带入最终镜像。
两个 Worker 共享 Node/安全工具基础层，各自只加对应 CLI 与一份项目安装包。
项目 wheel 在独立构建阶段生成，并在 CLI 安装之后安装；以后改项目代码不会使 CLI 的 npm 安装缓存失效。
保留 Node 22.22.0、Codex 0.158.0、Claude Code 2.1.123；不改 CLI 内部组件。
Node 开发头文件不进入最终镜像；GCC/make/nasm 保留以便题内编译。
Z3 同内容的两个动态库名字用硬链接共用实体。

知识库、Skill、经验卡与任务数据仍由运行时挂载，不烘焙进镜像，也不包含模型凭证。
Worker UID/GID 1000 与挂载机制保持一致。

## 构建与验收

```sh
TGA3_IMAGE_TAG=unified-candidate bash scripts/build-images.sh
```

验证包括：两种 shell 的解释器一致性、没有 venv/第二个 Python 命令、分发元数据无重复、
pip check、安全库运算、汇编反汇编、GCC 编译、GDB、真实 binwalk 签名识别、
ROP 工具、所有必需命令与项目各角色入口导入。候选通过后才切换运行标签。
完整任务机制另用隔离本地服务和原生 CLI 验证，不向真实题目注入测试操作。

基础镜像、Node 下载源、Debian 镜像站与网络参数沿用构建脚本；保持 TLS 校验。
旧镜像仅供回滚，不自动删除任务数据、数据库卷或旧镜像。
