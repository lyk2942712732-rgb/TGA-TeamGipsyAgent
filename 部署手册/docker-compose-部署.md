# TGA3 Docker Compose 部署说明

## 1 Compose 的实际边界

当前源码中的 `tga3/compose.yaml` 只管理 PostgreSQL。TGA3 控制面运行在宿主机，两个 Worker 由控制面通过 Docker SDK按任务动态创建。因此不要把 Worker 改为固定 Compose 服务，也不要期待 `docker compose up` 自动启动完整系统。

```text
docker compose
└── postgres 容器（长期运行）

宿主机
├── tga3 控制面（长期运行）
├── Nginx（生产环境）
└── Docker SDK -> Worker 容器（按任务创建和回收）
```

## 2 PostgreSQL 服务定义

源码配置的关键值如下：

| 项目 | 值 |
|---|---|
| 镜像 | `postgres:17-alpine` |
| 容器名 | `tga3-postgres` |
| 数据库 | `tga3` |
| 用户 | `tga3` |
| 默认密码 | `tga3` |
| 宿主端口 | `127.0.0.1:5433` |
| 数据卷 | `tga3-postgres-data` |
| 初始化脚本 | `./schema.sql` |

默认密码适合隔离的本地开发。生产环境应同时修改 `compose.yaml` 中的密码和 `config/runtime.json` 中 `postgres_dsn` 的密码，并限制文件权限。两处必须一致。

## 3 启动与检查

```bash
cd tga3
docker compose config
docker compose up -d --wait postgres
docker compose ps
docker compose exec postgres pg_isready -U tga3 -d tga3
```

查看日志：

```bash
docker compose logs --tail=200 postgres
docker compose logs -f postgres
```

停止但保留数据：

```bash
docker compose stop postgres
```

重新启动：

```bash
docker compose start postgres
```

删除容器但保留命名卷：

```bash
docker compose down
```

`docker compose down -v` 会删除数据库卷，只能用于确认数据可丢弃的开发或测试环境。

## 4 首次初始化

PostgreSQL 官方镜像仅在数据目录为空时执行 `/docker-entrypoint-initdb.d/001-schema.sql`。如果命名卷已经存在，修改 `schema.sql` 后重启容器不会自动更新表结构。

开发环境需要全新数据库时：

```bash
cd tga3
docker compose down -v
docker compose up -d --wait postgres
```

生产环境没有自动迁移机制。发现 schema 变化时，先备份数据库并编写显式迁移 SQL；不得用删除数据卷代替迁移。

## 5 数据备份

创建逻辑备份：

```bash
cd tga3
mkdir -p backups
docker compose exec -T postgres \
  pg_dump -U tga3 -d tga3 --format=custom \
  > "backups/tga3-$(date +%Y%m%d-%H%M%S).dump"
```

检查备份：

```bash
pg_restore --list backups/tga3-YYYYMMDD-HHMMSS.dump | head
```

恢复应在维护窗口内进行。先停止控制面，再向已准备好的空数据库执行：

```bash
docker compose exec -T postgres \
  pg_restore -U tga3 -d tga3 --clean --if-exists \
  < backups/tga3-YYYYMMDD-HHMMSS.dump
```

恢复命令会替换现有对象，执行前必须再次确认目标数据库和备份文件。

## 6 Worker 镜像

Worker 镜像由源码脚本构建：

```bash
cd tga3
chmod +x scripts/*.sh
./scripts/build-images.sh
```

国内网络可指定 Kali 镜像：

```bash
KALI_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/kali \
  ./scripts/build-images.sh
```

如宿主网络模式不可用：

```bash
DOCKER_BUILD_NETWORK=bridge ./scripts/build-images.sh
```

构建结果：

```bash
docker image inspect tga3-ctf-base:local >/dev/null
docker image inspect tga3-worker-openai:local >/dev/null
docker image inspect tga3-worker-claude:local >/dev/null
./scripts/verify-worker-image.sh tga3-ctf-base:local
```

## 7 动态 Worker 排查

列出 TGA3 创建的容器：

```bash
docker ps -a --filter label=tga3.task_id
```

查看某个 Worker 日志：

```bash
docker logs --tail=200 <容器名或容器ID>
```

Worker 的默认安全和资源参数来自 `config/runtime.json`：2 CPU、4096 MB、512 PIDs、非 root UID/GID 1000、丢弃全部 capability 后仅增加 `NET_RAW` 和 `SYS_PTRACE`，并启用 `no-new-privileges`。

## 8 常见误区

- Compose healthy 只表示数据库就绪，不表示控制面或前端已启动。
- Worker 镜像存在不表示 Worker 应常驻；没有任务时看不到 Worker 是正常状态。
- 宿主机 `localhost` 在 Worker 容器内不是宿主机，因此运行时使用 `host.docker.internal`。
- 切换 Docker 网络名称时，`runtime.json` 的 `docker.network` 必须指向已存在且允许访问宿主网关的网络。
