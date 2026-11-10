# TGA CTF Skills

来源：[ljagiello/ctf-skills](https://github.com/ljagiello/ctf-skills)，MIT。
固定版本：`2ad85d368a86c2bc1ce13143a3768f4e55fd1c61`。

九类包均通过只读 `/skills` 提供，所有 Worker 使用同一份文件。目录及正文按需读取，不向每轮对话注入全部正文。

- [ctf-web](ctf-web/SKILL.md)：Web：SQL 注入、SSTI、SSRF、鉴权、上传、反序列化与客户端漏洞。
- [ctf-pwn](ctf-pwn/SKILL.md)：Pwn：栈堆利用、ROP、格式化字符串、Shellcode、沙箱与内核。
- [ctf-crypto](ctf-crypto/SKILL.md)：Crypto：RSA、对称密码、ECC、格、PRNG 与密码协议分析。
- [ctf-reverse](ctf-reverse/SKILL.md)：Reverse：二进制、虚拟机、反混淆、字节码与仿真分析。
- [ctf-forensics](ctf-forensics/SKILL.md)：Forensics：磁盘内存、流量、文件格式、隐写与取证分析。
- [ctf-misc](ctf-misc/SKILL.md)：Misc：编码、音频、二维码、脚本沙箱与杂项分析。
- [ctf-ai-ml](ctf-ai-ml/SKILL.md)：AI/ML：对抗样本、模型分析、提示注入与机器学习安全。
- [ctf-osint](ctf-osint/SKILL.md)：OSINT：定位、公开资料、图像线索、域名与身份关联。
- [ctf-malware](ctf-malware/SKILL.md)：Malware：恶意代码、混淆脚本、协议、内存与动态分析。

## 安装范围与改动

保留所选包的 Markdown、包内脚本及 MIT 许可证；入口描述改为紧凑中文，并增加 TGA 的按需读取、复用现有环境和目标范围约定。
未执行上游依赖安装脚本，未新增系统包、Python 环境、MCP 服务或批量 PayloadsAllTheThings 克隆。
未安装依赖公网 Tavily 的 ctf-similar-search，也未引入上游的总调度 solve-challenge 或报告模板 ctf-writeup，避免替换项目现有流程。

## 托管评测边界

这些包含历史题解与具体解题示例，只用于普通主版本。禁止直接复制到受限托管镜像。
托管 Dockerfile 的允许列表继续排除 config/skills，本次不会将这些资料或既有答案、报告、经验卡打包进去。
