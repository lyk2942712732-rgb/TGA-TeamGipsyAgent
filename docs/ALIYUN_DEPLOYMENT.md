# TGA 阿里云公网部署手册

本文档面向一台 Ubuntu 22.04/24.04 ECS。目标是让指导老师可以通过公网访问 TGA Web，并由服务器本地调用 `mcp-security-hub` 的 Docker 镜像执行工具。

## 1. 推荐拓扑

```text
公网浏览器
  |
  | 80/443
  v
Nginx
  |-- /        -> TGA Web 静态文件
  |-- /api/*   -> 127.0.0.1:8000 FastAPI

FastAPI / TGA
  |
  | 本地 Docker 调用
  v
mcp-security-hub images
```

当前 TGA 不需要把 `mcp-security-hub` 作为公网 HTTP 服务暴露出去。MCP 工具镜像在服务器本地构建，TGA 运行时通过本地 Docker 调用。

## 2. ECS 与安全组

建议配置：

- Ubuntu 22.04 LTS 或 24.04 LTS
- 2 核 4G 起步，磁盘 40G 起步
- 安全组开放 `22/tcp`、`80/tcp`、`443/tcp`
- 不要向公网开放 Docker 端口
- 不要向公网直接开放 `8000/tcp`，让 Nginx 反代 `/api`

## 3. 安装系统依赖

```bash
sudo apt update
sudo apt install -y git curl nginx python3 python3-venv python3-pip docker.io docker-compose-plugin
sudo systemctl enable --now docker nginx
sudo usermod -aG docker $USER
```

重新登录 SSH，让 docker 用户组生效。

## 4. 放置代码

```bash
sudo mkdir -p /opt/tga
sudo chown -R $USER:$USER /opt/tga
cd /opt/tga

git clone <你的TGA仓库地址> TGA-TeamGipsyAgent
git clone https://github.com/FuzzingLabs/mcp-security-hub.git mcp-security-hub
```

如果仓库暂时没有推到 Git，也可以用 `scp` 或压缩包上传到 `/opt/tga/TGA-TeamGipsyAgent`。

## 5. 构建 MCP 工具镜像

先构建 MVP 必需工具：

```bash
cd /opt/tga/mcp-security-hub
docker compose build whatweb-mcp semgrep-mcp gitleaks-mcp
```

如果要做主动 Web 靶机测试，再构建：

```bash
docker compose build nmap-mcp nuclei-mcp ffuf-mcp sqlmap-mcp
```

检查：

```bash
cd /opt/tga/TGA-TeamGipsyAgent
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

export TGA_MCP_SECURITY_HUB_ROOT=/opt/tga/mcp-security-hub
python scripts/tga_mcp_healthcheck.py --hub-root "$TGA_MCP_SECURITY_HUB_ROOT"
```

三个基础镜像显示健康即可进入下一步。

## 6. 配置 TGA 后端

创建 `/opt/tga/TGA-TeamGipsyAgent/.env`：

```bash
TGA_RUN_ROOT=/opt/tga/TGA-TeamGipsyAgent/runs
TGA_MCP_SECURITY_HUB_ROOT=/opt/tga/mcp-security-hub

# 可选：OpenAI-compatible 国内模型或比赛指定 AI 安全网关
TGA_LLM_BASE_URL=
TGA_LLM_API_KEY=
TGA_LLM_MODEL=
```

当前 Week1 MVP 已有 LLM 适配层和连通性检查脚本，但默认 planner 仍以规则决策为主。配置模型后可以先检查：

```bash
source .venv/bin/activate
set -a
source .env
set +a
python scripts/tga_llm_healthcheck.py
```

## 7. systemd 托管后端

创建 `/etc/systemd/system/tga-api.service`：

```ini
[Unit]
Description=TGA FastAPI backend
After=network-online.target docker.service
Wants=network-online.target

[Service]
WorkingDirectory=/opt/tga/TGA-TeamGipsyAgent
EnvironmentFile=/opt/tga/TGA-TeamGipsyAgent/.env
ExecStart=/opt/tga/TGA-TeamGipsyAgent/.venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now tga-api
sudo systemctl status tga-api --no-pager
curl http://127.0.0.1:8000/api/health
```

注意：运行 `tga-api` 的系统用户需要能访问 Docker。为了简化 MVP，可以先用当前登录用户启动服务；正式环境建议创建专用用户并只授予必要权限。

## 8. 构建前端

如果前端和后端同域名部署，构建时让 API 走同源 `/api`：

```bash
cd /opt/tga/TGA-TeamGipsyAgent/apps/web
npm install
VITE_TGA_API_BASE= npm run build
```

构建产物在：

```text
/opt/tga/TGA-TeamGipsyAgent/apps/web/dist
```

## 9. Nginx 反代

创建 `/etc/nginx/sites-available/tga`：

```nginx
server {
    listen 80;
    server_name 你的公网IP或域名;

    root /opt/tga/TGA-TeamGipsyAgent/apps/web/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/tga /etc/nginx/sites-enabled/tga
sudo nginx -t
sudo systemctl reload nginx
```

访问：

```text
http://你的公网IP/
```

## 10. HTTPS

有域名时建议启用 HTTPS：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d 你的域名
```

## 11. 公开测试注意事项

- 只给指导老师测试授权靶机，前端的“授权范围”必须填写靶机 IP/域名。
- 不要把未授权公网目标填入 TGA。
- 不要开放 Docker API。
- `runs/` 会保存证据、报告和执行记录，演示前可以保留几条成功样例。
- 如果老师只检查界面和报告，不需要打开主动扫描工具；如果要实测靶机，再构建并启用 `nmap/nuclei/ffuf/sqlmap` 等镜像。

## 12. 常用排错

查看后端日志：

```bash
sudo journalctl -u tga-api -f
```

检查 MCP：

```bash
cd /opt/tga/TGA-TeamGipsyAgent
source .venv/bin/activate
export TGA_MCP_SECURITY_HUB_ROOT=/opt/tga/mcp-security-hub
python scripts/tga_mcp_healthcheck.py --hub-root "$TGA_MCP_SECURITY_HUB_ROOT"
```

重建前端：

```bash
cd /opt/tga/TGA-TeamGipsyAgent/apps/web
VITE_TGA_API_BASE= npm run build
sudo systemctl reload nginx
```
