# 云端部署手册：通用 Ubuntu 云服务器 + Docker Compose

本手册面向**任意一台 Ubuntu 云服务器**（阿里云 / 腾讯云 / 华为云 / 自建虚拟机均可），从零完成本系统的部署、验证、升级与回滚。不依赖任何云厂商控制台，控制台操作仅限"开放 80 端口 / 拿到公网 IP"这类通用步骤。

- 本地零 Docker 开发：见 [README](../README.md#本地零-docker-启动推荐) 与 `scripts/dev-start.sh`、`scripts/dev-start.ps1`
- CodeArts 流水线（赛事加分项）：见本文 [第 13 节](#13-可选codearts-流水线三阶段)，属可选附加内容

## 0. 一条命令部署（推荐，零配置）

**无需 `.env`、无需手填密钥、无需预装 MySQL。** 数据库（`mysql:8.0`）由 Compose 自带并随 `up` 一起启动；密钥使用内置默认值；后端每次启动自动建表并灌入演示数据。

```bash
# 服务器只需安装 Docker（Ubuntu）
curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun
systemctl enable --now docker

git clone https://atomgit.com/IceFire_Icer/manage_system.git
cd manage_system
docker compose up -d --build
```

首次启动约 3-8 分钟（构建镜像 + 拉取 mysql:8.0）：

1. `db` 容器初始化 `manage_system` 库（utf8mb4），健康检查通过后才启动 `backend`
2. `backend` 执行 `alembic upgrade head` 建表，再执行 `scripts/seed_demo.py` 灌入演示数据（幂等，重复启动不会重复写入）
3. `web`（nginx）等待 `backend` 健康后启动并监听 80

访问 `http://<服务器IP>/`，演示账号：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| teacher01 | `Teacher@123` | 教师 |
| admin01 | `Admin@123` | 班委 |
| student01 | `Student@123` | 学生 |

演示数据即"紧急 / 一般 / 观察"三级补货场景，登录后「补货建议」页可直接看到分级卡片。

> **安全提示**：内置默认密钥（`MYSQL_ROOT_PASSWORD=ManageSystem2026`、`JWT_SECRET_KEY=manage-system-demo-…`）**仅供演示**。生产环境建议 `cp .env.example .env` 覆盖这两个变量后再 `docker compose up -d`。第 `1`~`14` 节为手动/进阶部署与运维说明：安装与首次启动已包含在本节，可跳过第 `3`~`6` 节；后续运维、升级、备份与排错仍按下文执行。

## 1. 部署拓扑

```
浏览器 ──HTTP:80──> web 容器（nginx:alpine，托管 Vue 静态产物）
                       └──/api/──> backend 容器（python:3.12-slim，uvicorn 单进程, 8000）
                                        └──> db 容器（mysql:8.0, 3306）
```

- 仅 `web` 暴露宿主机 `80` 端口（可通过 `WEB_PORT` 改为其他端口）
- `backend` 与 `db` 只在 Compose 内部网络中可达，不对外开放 8000 / 3306
- 数据持久化在命名卷 `manage-system_mysql_data`
- 推荐配置：2 核 4G、磁盘 ≥ 20G、Ubuntu 22.04/24.04

## 2. 前置条件

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Ubuntu 20.04+（本文以 22.04/24.04 为准） |
| 硬件 | 2C4G 起，磁盘 ≥ 20G |
| 网络 | 安全组 / 防火墙放行 TCP 80（或自定义 `WEB_PORT`） |
| 账号 | 具备 `sudo` 权限的 SSH 账号 |
| 代码 | AtomGit 仓库 `https://atomgit.com/IceFire_Icer/manage_system.git` 访问权限 |

> 本文不涉及开通云主机、创建镜像仓库等操作，请先在云厂商控制台完成，并在安全组放行 80 端口。

## 3. 安装 Docker Engine 与 Compose 插件

```bash
# 1) 基础依赖
sudo apt-get update
sudo apt-get install -y ca-certificates curl git

# 2) 添加 Docker 官方 GPG 与 apt 源
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 3) 安装 Docker Engine + Compose 插件
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 4) 当前用户加入 docker 组（重新登录后生效）
sudo usermod -aG docker "$USER"
newgrp docker

# 5) 验证
docker version
docker compose version
```

> 若 `download.docker.com` 访问缓慢，可替换为云厂商提供的 Docker CE 镜像源；镜像拉取慢可配置 `/etc/docker/daemon.json` 的 `registry-mirrors` 后 `sudo systemctl restart docker`。

## 4. 获取代码

### 方式 A：服务器直接克隆（推荐）

```bash
sudo mkdir -p /opt/manage_system && sudo chown "$USER":"$USER" /opt/manage_system
git clone https://atomgit.com/IceFire_Icer/manage_system.git /opt/manage_system
cd /opt/manage_system
```

### 方式 B：本地打包上传（服务器无法访问仓库时）

在本地仓库根目录执行（排除本地依赖与密钥）：

```bash
tar --exclude=backend/venv --exclude=frontend/node_modules --exclude=frontend/dist \
    --exclude=.env --exclude=backend/.env --exclude=.git \
    -czf manage_system.tar.gz .

scp manage_system.tar.gz <user>@<服务器IP>:/opt/manage_system/
```

服务器上解压到 `/opt/manage_system` 即可。

## 5.（可选）覆盖默认密钥 `.env`

**跳过本节亦可正常启动**：Compose 已内置默认 `MYSQL_ROOT_PASSWORD` 与 `JWT_SECRET_KEY`。仅当需要为生产环境替换默认密钥时才执行本节的构建。手动章节（第 5、6 节）仅保留为进阶步骤。

```bash
cd /opt/manage_system
cp .env.example .env
chmod 600 .env

# 生成随机 JWT 密钥并写入
JWT_SECRET="$(openssl rand -hex 32)"
sed -i "s/^JWT_SECRET_KEY=.*/JWT_SECRET_KEY=${JWT_SECRET}/" .env

# 设置数据库 root 密码（示例：仅字母数字，避免 URL 转义问题）
DB_PASS="$(openssl rand -hex 12)"
sed -i "s/^MYSQL_ROOT_PASSWORD=.*/MYSQL_ROOT_PASSWORD=${DB_PASS}/" .env

# 按需填写 LLM（留空则智能补货自动降级为规则模板）
vi .env
```

> 若数据库卷已用默认密码初始化过，之后修改 `MYSQL_ROOT_PASSWORD` 会导致 `Access denied`；需 `docker compose down -v`（**会删数据**）或进入容器 `ALTER USER` 重置后再启动。

`.env` 关键字段：

| 变量 | 说明 |
| --- | --- |
| `MYSQL_ROOT_PASSWORD` | MySQL root 密码；会拼进 `DATABASE_URL`，**建议只用字母数字** |
| `JWT_SECRET_KEY` | JWT 签名密钥，至少 32 位随机字符；变更后旧登录态失效 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | DeepSeek 等 OpenAI 兼容接口；留空则只用规则引擎 |
| `WEB_PORT` | 对外 HTTP 端口，默认 `80` |
| `IMAGE_TAG` | 镜像版本标签，默认 `latest`，升级/回滚时切换 |

> `.env` 已在 `.gitignore` 中，**不要提交**。密码若包含 `@ : / # ?` 等字符，需要 URL 编码（`DATABASE_URL` 由 Compose 自动拼接，未编码会连不上库）。

`backend/.env` 是**本地零 Docker 开发**用的配置，云端不使用它（容器内配置全部来自 Compose 的 `environment`）。

## 6. 构建并启动

```bash
cd /opt/manage_system
docker compose config          # 静态校验（不连 daemon，配置错误会立即报出；无 .env 也可通过）
docker compose up -d --build   # 首次构建 + 后台启动，约 3-8 分钟（视网络）
docker compose ps              # 三个服务应为 running / healthy
```

首次启动时：

1. `db` 初始化数据库 `manage_system`（utf8mb4），健康检查通过后才启动 backend
2. `backend` 启动脚本先执行 `alembic upgrade head`（建表），再执行 `scripts/seed_demo.py` 写入演示数据（幂等），最后启动 uvicorn
3. `web` 等待 backend 健康后启动 nginx

## 7. 数据库迁移与种子数据

正常流程无需手工操作：`backend` 容器每次启动都会执行 `alembic upgrade head`（幂等）。

需要手动执行时：

```bash
# 查看当前版本
docker compose exec backend alembic current

# 执行到最新
docker compose exec backend alembic upgrade head

# 查看迁移历史
docker compose exec backend alembic history
```

演示账号在首次迁移时写入（见 README「演示账号」）：`teacher01 / Teacher@123`、`admin01 / Admin@123`、`student01 / Student@123`。

> **上线后请立即修改演示账号密码**（教师可在系统内维护用户，或直接改数据库并保证口令哈希口径一致）。

## 8. 访问验证

```bash
# 服务器本机健康检查
curl -fsS http://127.0.0.1/api/v1/health
# 期望：{"status":"ok"}

# 检查首页
curl -sI http://127.0.0.1/ | head -1
# 期望：HTTP/1.1 200 OK
```

浏览器访问 `http://<服务器公网IP>/`（或 `http://<IP>:<WEB_PORT>/`）：

1. 自动跳转 `/login`
2. 用 `teacher01 / Teacher@123` 登录，进入「库存总览」
3. 切换 `admin01` 验证耗材档案、用户管理入口
4. 打开「补货建议」，确认三级分级卡片（未配置 LLM 时使用规则模板，不影响功能）

## 9. 常用运维命令

```bash
cd /opt/manage_system

docker compose ps                 # 状态
docker compose logs -f backend    # 后端实时日志（Ctrl+C 退出）
docker compose logs --tail=200 web
docker compose logs --tail=200 db

docker compose restart backend    # 只重启后端
docker compose stop               # 停止（保留数据卷）
docker compose down               # 停止并删除容器（保留数据卷）
docker compose down -v            # 连同数据卷一起删除（会丢数据，慎用）

docker compose exec backend sh    # 进入后端容器
docker compose exec db mysql -uroot -p manage_system   # 进入数据库
```

资源占用快速排查：

```bash
docker stats --no-stream
docker system df
```

## 10. 升级

```bash
cd /opt/manage_system

# 0) 升级前先备份数据库（见第 11 节）
# 1) 记录当前运行版本，便于回滚
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' \
  "$(docker compose ps -q backend)" 2>/dev/null || true
docker image ls manage-system-backend manage-system-web

# 2) 拉取新代码
git pull --ff-only

# 3) 重新构建并滚动重启（backend 会自动执行迁移）
docker compose up -d --build

# 4) 验证
docker compose ps
curl -fsS http://127.0.0.1/api/v1/health
```

升级后清理旧镜像：

```bash
docker image prune -f
```

## 11. 回滚与数据备份

### 11.1 镜像回滚（推荐）

升级前先给当前镜像打版本标签，升级出问题时切换回来：

```bash
# 升级前：给当前镜像打上版本标签，并记录到 .env 的 IMAGE_TAG（可选）
TAG="v$(date +%Y%m%d-%H%M)"
docker tag manage-system-backend:latest "manage-system-backend:${TAG}"
docker tag manage-system-web:latest "manage-system-web:${TAG}"

# 出问题时：用旧标签启动（注意不要加 --build，否则会覆盖标签镜像）
IMAGE_TAG="${TAG}" docker compose up -d --no-build

# 确认回滚成功
docker compose ps
```

> 数据库迁移通常是单向的：回滚镜像前请先确认旧版本代码兼容当前表结构；不确定时优先「修复新版本」或「恢复备份」。

### 11.2 代码回滚

```bash
git log --oneline -10
git checkout <上一个稳定 commit 或 tag>
docker compose up -d --build
```

### 11.3 备份与恢复

```bash
cd /opt/manage_system
set -a; . ./.env; set +a     # 载入 MYSQL_ROOT_PASSWORD

# 备份
docker compose exec -T db mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction --routines manage_system > "backup_$(date +%F_%H%M).sql"

# 恢复（先停应用写入，避免并发写）
docker compose stop backend
docker compose exec -T db mysql -uroot -p"$MYSQL_ROOT_PASSWORD" manage_system < backup_2026-11-27_1200.sql
docker compose start backend
```

建议把备份输出目录（或 `mysql_data` 卷目录）纳入云盘快照/定时任务。

## 12. 常见问题排查

| 现象 | 排查与处理 |
| --- | --- |
| `permission denied /var/run/docker.sock` | 未加入 docker 组：`sudo usermod -aG docker $USER` 后**重新登录** |
| `docker compose up` 报 `port is already allocated` | 80 被宿主机 nginx/apache 占用：`sudo ss -lntp \| grep :80`；或把 `.env` 的 `WEB_PORT` 改为 8080 并放行安全组 |
| `db` 反复重启 / `Access denied for user 'root'` | 初始化时的密码已固化在数据卷；改过 `.env` 密码需要 `docker compose down -v`（**会删数据**）或进入容器 `ALTER USER` |
| `backend` 一直 `unhealthy` | `docker compose logs backend`；常见原因：密码含特殊字符导致 `DATABASE_URL` 非法、db 尚未就绪、迁移失败 |
| 页面正常但刷新子路由 404 | 确认 `frontend/nginx.conf` 中 `try_files ... /index.html` 存在（本仓库已内置），并重建 web 镜像 |
| 登录后立刻回到登录页 / 401 | 改过 `JWT_SECRET_KEY` 会使旧 Cookie 失效，重新登录；检查服务器时间是否准确 |
| 补货建议只有模板文案 | `LLM_API_KEY` 为空或不可用，属预期降级；填好 key 后 `docker compose up -d` 生效 |
| 镜像拉取 / 构建超慢 | 配置 Docker `registry-mirrors`；前端 Dockerfile 支持 `--build-arg NPM_REGISTRY=...`，后端支持 `--build-arg PIP_INDEX_URL=...` |
| 磁盘占满 | `docker system df`；`docker image prune -af`；检查 `mysql_data` 增长与备份文件 |
| 时间显示为 UTC | 容器已设 `TZ=Asia/Shanghai`；确认宿主机时区 `timedatectl` |

抓取完整日志用于排查：

```bash
docker compose logs --no-color > /tmp/compose-logs.txt
docker inspect --format '{{json .State.Health}}' "$(docker compose ps -q backend)"
```

## 13. 可选：CodeArts 流水线三阶段

赛事要求依托华为云 CodeArts 完成 CI/CD 发布。本节给出与仓库结构匹配的三个可执行脚本，**具体界面以 CodeArts 控制台为准**；不使用华为云时，也可用任意 CI（GitHub Actions / GitLab CI / Jenkins）照搬同一套脚本。

三个脚本均可本地直接执行，也可在 CodeArts 流水线中创建阶段并调用：

| 阶段 | 脚本 | 说明 |
| --- | --- | --- |
| 1 前端构建 | `scripts/ci/stage1-frontend-build.sh` | `npm ci` + `npm run build`，校验 `frontend/dist` |
| 2 后端测试 | `scripts/ci/stage2-backend-test.sh` | 创建/复用 venv，`pip install` + `pytest` + `ruff check .` |
| 3 镜像发布 | `scripts/ci/stage3-image-publish.sh` | 构建并推送 backend/web 镜像，必需 `IMAGE_REGISTRY`、`IMAGE_TAG` |

### 阶段 1：前端构建（node:22）

```bash
bash scripts/ci/stage1-frontend-build.sh
# 归档产物：frontend/dist
# 可用 NPM_REGISTRY 覆盖依赖源（默认 https://registry.npmmirror.com）
```

### 阶段 2：后端测试（python:3.12）

```bash
bash scripts/ci/stage2-backend-test.sh
# 可用 VENV_DIR 复用已有虚拟环境，PIP_INDEX_URL 覆盖依赖源
```

### 阶段 3：镜像构建推送 + 服务器发布

前置：在 SWR（或任意镜像仓库）创建 `manage-system-backend`、`manage-system-web` 两个仓库，并把登录凭据、服务器 SSH 私钥、host/端口配置为流水线**凭据/参数**，不要写进仓库。

```bash
IMAGE_REGISTRY="${SWR_REGISTRY}" \
IMAGE_TAG="${BUILD_NUMBER:-$(date +%Y%m%d%H%M)}" \
bash scripts/ci/stage3-image-publish.sh
```

`stage3` 缺少 `IMAGE_REGISTRY` 或 `IMAGE_TAG` 时会输出用法并以非零码退出，流水线会立即失败，避免误推镜像。

仓库脚本只负责构建与推送；发布到 ECS 仍可在流水线追加一个 SSH 步骤：

```bash
ssh -o StrictHostKeyChecking=accept-new "${DEPLOY_USER}@${DEPLOY_HOST}" \
  "cd /opt/manage_system && \
   git pull --ff-only && \
   IMAGE_TAG=${IMAGE_TAG} docker compose pull && \
   IMAGE_TAG=${IMAGE_TAG} docker compose up -d --no-build && \
   docker compose ps"
```

在校验发布结果时，流水线可追加：

```bash
ssh "${DEPLOY_USER}@${DEPLOY_HOST}" "curl -fsS http://127.0.0.1/api/v1/health"
```

### 版本标签与回滚

- 每次流水线都用 `BUILD_NUMBER`（或时间戳）作为 `IMAGE_TAG`，镜像与发布版本一一对应
- 回滚 = 在服务器上以历史标签重启：

```bash
cd /opt/manage_system
IMAGE_TAG=<上一次成功标签> docker compose up -d --no-build
```

- 若拉取的是远程镜像，需先 `IMAGE_TAG=<tag> docker compose pull` 再启动

## 14. 上线安全清单

- [ ] `.env` 权限 600，未提交到仓库（`git status` 确认无 `.env`）
- [ ] `JWT_SECRET_KEY` 为随机 32+ 字符，未使用默认占位值
- [ ] 修改 `teacher01 / admin01 / student01` 演示密码
- [ ] 安全组仅放行 22 / 80（或自定义端口），不暴露 3306 / 8000
- [ ] 配置数据库定期备份与磁盘快照
- [ ] 如需 HTTPS：在前置 nginx/Caddy 或云负载均衡上接入证书（当前演示为 HTTP）
- [ ] 定期 `docker compose logs` 巡检与 `docker image prune` 清理