# 云端部署手册：通用 Ubuntu 云服务器 + Docker Compose

本手册面向**任意一台 Ubuntu 云服务器**（阿里云 / 腾讯云 / 华为云 / 自建虚拟机均可），从零完成本系统的部署、验证、升级与回滚。不依赖任何云厂商控制台，控制台操作仅限「开放 4900 端口 / 拿到公网 IP」这类通用步骤。

- 本地零 Docker 开发：见 [README](../README.md#本地开发) 与 `scripts/dev-start.sh`、`scripts/dev-start.ps1`
- GitHub Actions 与 GHCR 镜像发布：见本文 [第 13 节](#13-github-actions-与-ghcr-镜像发布)

## 0. 快速部署（唯一主路径：拉取镜像）

Compose 内置 `mysql:8.0`、nginx 与 uvicorn，服务器只需安装 Docker；应用镜像由 GitHub Actions 在仓库端构建并发布到 GHCR，**服务器只拉取、不本地构建**。首次启动自动建表并创建管理员账号。

仓库将「拉取」与「源码构建」拆为两个 compose 文件：

- `docker-compose.yml`：默认文件，只有 `image`，不含 `build`，`up` 不会触发源码构建
- `docker-compose.build.yml`：可选覆盖文件，**仅本地开发/调试使用**，服务器正式部署不使用

### 0.1 部署（唯一主路径）

**第 1 步：安装 Docker Engine 与 Compose 插件（Ubuntu）**

```bash
curl -fsSL https://get.docker.com | sh -s -- --mirror Aliyun
systemctl enable --now docker
```

安装后确认 Compose 插件可用（缺失会报 `docker: unknown command: docker compose`）：

```bash
docker compose version
# 报 unknown command 时安装插件：
sudo apt-get update && sudo apt-get install -y docker-compose-plugin
```

**第 2 步：克隆仓库并配置生产密钥**

仓库地址必须是 `https://github.com/IceFireIcer/manage_system.git`：

```bash
git clone https://github.com/IceFireIcer/manage_system.git
cd manage_system

# 生产环境必须替换密钥（详见第 5 节）
cp .env.example .env
chmod 600 .env
# 编辑 .env：MYSQL_ROOT_PASSWORD、JWT_SECRET_KEY 填随机值
```

**第 3 步：拉取 GHCR 镜像并启动（不要构建）**

```bash
# 私有包需先登录 GHCR（公开包可跳过）
docker login ghcr.io -u <GitHub 用户名>

docker compose config          # 静态校验：不应出现 backend/web 的 build 段
docker compose pull
docker compose up -d
docker compose ps
```

镜像名必须是 `ghcr.io/icefireicer/labstock-api` 与 `ghcr.io/icefireicer/labstock-web`。若 `pull` 报 404 或镜像名不匹配，先核对 `docker compose config | grep "image: ghcr.io"`，并确认仓库地址不是 `IceFirerer` 等拼写错误。

首次启动约 1-3 分钟：

1. `db` 容器初始化 `manage_system` 库（utf8mb4），健康检查通过后才启动 `backend`
2. `backend` 执行 `alembic upgrade head` 建四张核心表，再执行 `scripts/bootstrap_admin.py` 创建管理员（幂等）
3. `web`（nginx）等待 `backend` 健康后启动并监听 `WEB_PORT`（默认 4900）

**第 4 步：获取管理员初始密码**

首次启动在日志中打印管理员账号与初始密码：

```bash
docker compose logs backend | grep bootstrap
# [bootstrap] 已创建管理员账号: admin；初始密码: <随机密码>（请首次登录后立即修改并妥善保存）
```

- 如需指定账号/密码，先在 `.env` 设置 `ADMIN_INIT_ACCOUNT` / `ADMIN_INIT_PASSWORD`（及可选 `ADMIN_INIT_NAME`）再启动
- 已存在启用的管理员时日志为 `[bootstrap] 管理员账号已存在，跳过`；`ADMIN_INIT_PASSWORD` 不会重置已有账号密码
- 修改初始密码的方法见 [README「管理员初始化」](../README.md#管理员初始化)

访问 `http://<服务器IP>:4900/` 登录；新建耗材档案与用户后即可开始日常使用。

> GHCR 包默认私有：可在 GitHub Packages 将 `labstock-api` / `labstock-web` 设为 public；或服务器使用带 `read:packages` 权限的 PAT 执行 `docker login ghcr.io -u <GitHub 用户名>`。拉取慢时优先按第 3.1 节加速或重试；仍失败时再把镜像转存自有仓库。**本地构建不是服务器部署路径**，仅作无法访问 GHCR 时的临时手段（见 0.2）。

> **安全提示**：Compose 内置的默认 `MYSQL_ROOT_PASSWORD` 与 `JWT_SECRET_KEY` 仅用于首次开箱启动，**不具备生产安全性**。生产部署必须通过 `.env` 覆盖。第 `1`~`14` 节为完整部署与运维说明：安装与首次启动已包含在本节，可跳过第 `3`~`6` 节；后续运维、升级、备份与排错仍按下文执行。

### 0.2 可选：本地开发构建（服务器正式部署不使用）

**仅用于本地开发/调试，或服务器完全无法访问 GHCR 时的临时手段；服务器正式部署一律使用 0.1 的 `pull` 主路径，不执行本节命令。**

不依赖 GHCR，直接从源码构建：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
docker compose ps
```

国内网络可叠加基础镜像加速源（第三方地址可用性由使用者自行确认）：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml build \
  --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-alpine \
  --build-arg NGINX_IMAGE=docker.m.daocloud.io/library/nginx:alpine
docker compose up -d
```

构建首次启动约 3-8 分钟，启动后同样按 0.1 第 4 步获取管理员初始密码。

## 1. 部署拓扑

```
浏览器 ──HTTP:4900──> web 容器（nginx:alpine，托管 Vue 静态产物）
                       └──/api/──> backend 容器（python:3.12-slim，uvicorn 单进程, 8000）
                                        └──> db 容器（mysql:8.0, 3306）
```

- 仅 `web` 暴露宿主机 `4900` 端口（可通过 `WEB_PORT` 改为其他端口）
- `backend` 与 `db` 只在 Compose 内部网络中可达，不对外开放 8000 / 3306
- 数据持久化在命名卷 `labstock_mysql_data`
- 推荐配置：2 核 4G、磁盘 ≥ 20G、Ubuntu 22.04/24.04

## 2. 前置条件

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Ubuntu 20.04+（本文以 22.04/24.04 为准） |
| 硬件 | 2C4G 起，磁盘 ≥ 20G |
| 网络 | 安全组 / 防火墙放行 TCP 4900（或自定义 `WEB_PORT`） |
| 账号 | 具备 `sudo` 权限的 SSH 账号 |
| 代码 | GitHub 仓库 `https://github.com/IceFireIcer/manage_system.git` 访问权限 |

> 本文不涉及开通云主机、创建镜像仓库等操作，请先在云厂商控制台完成，并在安全组放行 4900 端口。

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

### 3.1 国内网络优化

**Docker Hub 基础镜像加速**（`mysql:8.0`、`python:3.12-slim`、`node:22-alpine`、`nginx:alpine` 均来自 Docker Hub）：

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "registry-mirrors": ["https://<你的加速器地址>"]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

`<你的加速器地址>` 请替换为自建或已订阅的镜像加速器地址，第三方公共加速器可用性由使用者自行确认。配置后 `docker compose pull` 会走加速源。

**GHCR 拉取慢**（`ghcr.io` 无通用公共加速器）：

- **首选仍是 `docker compose pull`**：先确认 `docker login ghcr.io` 已完成，再用 `docker compose pull` 重试（可多次重试或错峰拉取）
- 可选：把镜像转存到阿里云 ACR / 华为云 SWR 后改 `image:` 指向自有仓库；或 `docker save` / `docker load` 离线导入
- 仅作最后手段：本地源码构建（0.2 节，**非服务器正式部署路径**）

**构建时覆盖基础镜像源**（仅 0.2 可选构建路径使用，默认值与 CI 一致）：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml build \
  --build-arg BASE_IMAGE=docker.m.daocloud.io/library/python:3.12-slim \
  --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-alpine \
  --build-arg NGINX_IMAGE=docker.m.daocloud.io/library/nginx:alpine
docker compose up -d
```

**镜像名核对**：应用镜像名固定为 `ghcr.io/icefireicer/labstock-api` 与 `ghcr.io/icefireicer/labstock-web`。若拉取时报 404 或本地 `docker image ls` 显示其他拼写，请确认使用的是最新版 `docker-compose.yml`（`docker compose config | grep "image: ghcr.io"`）。

## 4. 获取代码

### 方式 A：服务器直接克隆（推荐）

```bash
sudo mkdir -p /opt/manage_system && sudo chown "$USER":"$USER" /opt/manage_system
git clone https://github.com/IceFireIcer/manage_system.git /opt/manage_system
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

## 5. 配置生产密钥（生产必做）

内置默认密钥仅供首次启动，生产必须写入自有密钥：

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

# 可选：指定首次创建的管理员账号/密码；不填则随机生成并打印到日志
# ADMIN_INIT_ACCOUNT=admin
# ADMIN_INIT_PASSWORD=<强密码>

# 按需填写 LLM（留空则智能补货降级为规则模板）
vi .env
```

> 若数据库卷已用默认密码初始化过，之后修改 `MYSQL_ROOT_PASSWORD` 会导致 `Access denied`；需 `docker compose down -v`（**会删数据**）或进入容器 `ALTER USER` 重置后再启动。

`.env` 关键字段：

| 变量 | 说明 |
| --- | --- |
| `MYSQL_ROOT_PASSWORD` | MySQL root 密码；会拼进 `DATABASE_URL`，**建议只用字母数字**；生产必填 |
| `JWT_SECRET_KEY` | JWT 签名密钥，至少 32 位随机字符；变更后旧登录态失效；生产必填 |
| `ADMIN_INIT_ACCOUNT` / `ADMIN_INIT_PASSWORD` / `ADMIN_INIT_NAME` | 首次创建的管理员账号、密码、姓名；仅首次创建生效 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容接口；留空则只用规则引擎 |
| `WEB_PORT` | 对外 HTTP 端口，默认 `4900` |
| `IMAGE_TAG` | 应用镜像版本标签，默认 `latest`；升级/回滚切换为 `sha-<short>` 或 `v*` 版本标签 |

> `.env` 已在 `.gitignore` 中，**不要提交**。密码若包含 `@ : / # ?` 等字符，需要 URL 编码（`DATABASE_URL` 由 Compose 自动拼接，未编码会连不上库）。

`backend/.env` 是**本地零 Docker 开发**用的配置，云端不使用它（容器内配置全部来自 Compose 的 `environment`）。

## 6. 拉取镜像并启动（推荐）

```bash
cd /opt/manage_system
docker compose config          # 静态校验（不连 daemon，配置错误会立即报出；输出中 backend/web 不应有 build 段）
docker login ghcr.io -u <GitHub 用户名>   # 私有包需要；公开包可跳过
docker compose pull            # 拉取 ghcr.io/icefireicer/labstock-api 与 labstock-web
docker compose up -d           # 后台启动
docker compose ps              # 三个服务应为 running / healthy
```

### 6.1 本地开发构建（可选备选，服务器不使用）

**仅用于本地开发/调试或服务器无法访问 GHCR 时的临时手段，服务器正式部署请使用上面的 `pull` 主路径。** 不依赖 GHCR、直接从源码构建时使用：

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build   # 首次构建 + 后台启动，约 3-8 分钟（视网络）
docker compose ps
```

首次启动时：

1. `db` 初始化数据库 `manage_system`（utf8mb4），健康检查通过后才启动 backend
2. `backend` 启动脚本先执行 `alembic upgrade head`（建四张核心表），再执行 `scripts/bootstrap_admin.py` 创建管理员（幂等），最后启动 uvicorn
3. `web` 等待 backend 健康后启动 nginx

随后按第 0.1 节第 4 步从日志获取管理员初始密码。

## 7. 数据库迁移与管理员初始化

正常流程无需手工操作：`backend` 容器每次启动都会执行 `alembic upgrade head`（幂等）与管理员自举。

需要手动执行时：

```bash
# 查看当前版本
docker compose exec backend alembic current

# 执行到最新
docker compose exec backend alembic upgrade head

# 查看迁移历史
docker compose exec backend alembic history

# 手动执行管理员自举（已存在启用的管理员时自动跳过）
docker compose exec backend python scripts/bootstrap_admin.py
```

> 系统不预置任何业务数据或账号。首次登录使用 bootstrap 创建的管理员；初始密码获取与修改见第 0.1 节第 4 步与 [README](../README.md#管理员初始化)。

## 8. 访问验证

```bash
# 服务器本机健康检查
curl -fsS http://127.0.0.1:4900/api/v1/health
# 期望：{"status":"ok"}

# 检查首页
curl -sI http://127.0.0.1:4900/ | head -1
# 期望：HTTP/1.1 200 OK

# 确认管理员已初始化
docker compose logs backend | grep bootstrap
```

浏览器访问 `http://<服务器公网IP>:4900/`（或 `http://<IP>:<WEB_PORT>/`）：

1. 自动跳转 `/login`
2. 用 bootstrap 打印的管理员账号登录，进入「库存总览」
3. 在「耗材档案」新建耗材并在「用户管理」新建教师/学生账号
4. 打开「补货建议」：无低库存耗材时列表为空属正常；录入数据后按三级分级展示（未配置 LLM 时使用规则模板，不影响功能）

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
docker compose config | grep "image: ghcr.io"
docker image ls ghcr.io/icefireicer/labstock-api ghcr.io/icefireicer/labstock-web
git log --oneline -1

# 2) 拉取新代码
git pull --ff-only

# 3) 拉取新镜像并滚动重启（backend 会自动执行迁移）
docker compose pull
docker compose up -d

# 4) 验证
docker compose ps
curl -fsS http://127.0.0.1:4900/api/v1/health
```

升级后清理旧镜像：

```bash
docker image prune -f
```

> 升级不会重置或重建管理员账号；已存在的启用管理员会被 bootstrap 跳过。

## 11. 回滚与数据备份

### 11.1 镜像回滚（推荐）

GitHub Actions 每次推送都会生成 `sha-<short>` 标签，推送 `v*` 时另有版本标签；升级出问题时切回历史标签：

```bash
cd /opt/manage_system

# 查看可用标签：GitHub Packages 页面，或本地 git tag
# 使用历史 sha-<short> 或 v* 版本标签回滚
IMAGE_TAG=sha-abc1234 docker compose pull
IMAGE_TAG=sha-abc1234 docker compose up -d

# 确认回滚成功
docker compose ps
```

仅在本地开发构建过镜像时，回滚前先用 `docker tag` 给当前镜像打本地备份标签，再以 `IMAGE_TAG=<标签> docker compose up -d` 启动（服务器部署不使用本地构建，无需此步骤）。

> 数据库迁移通常是单向的：回滚镜像前请先确认旧版本代码兼容当前表结构；不确定时优先「修复新版本」或「恢复备份」。

### 11.2 代码回滚

```bash
git log --oneline -10
git checkout <上一个稳定 commit 或 tag>
docker compose pull
docker compose up -d
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
| `docker compose up` 报 `port is already allocated` | 4900 被宿主机 nginx/apache 占用：`sudo ss -lntp \| grep :4900`；或把 `.env` 的 `WEB_PORT` 改为 4901 并放行安全组 |
| `db` 反复重启 / `Access denied for user 'root'` | 初始化时的密码已固化在数据卷；改过 `.env` 密码需要 `docker compose down -v`（**会删数据**）或进入容器 `ALTER USER` |
| `backend` 一直 `unhealthy` | `docker compose logs backend`；常见原因：密码含特殊字符导致 `DATABASE_URL` 非法、db 尚未就绪、迁移失败 |
| 日志中看不到 `[bootstrap]` 初始密码 | 多为管理员已存在（`管理员账号已存在，跳过`）；如确实缺失，执行 `docker compose exec backend python scripts/bootstrap_admin.py` 查看输出 |
| 忘记管理员密码 | 使用 [README「管理员初始化」](../README.md#管理员初始化) 的容器内脚本直接更新哈希 |
| 页面正常但刷新子路由 404 | 确认 `frontend/nginx.conf` 中 `try_files ... /index.html` 存在（本仓库已内置），并重建 web 镜像 |
| 登录后立刻回到登录页 / 401 | 改过 `JWT_SECRET_KEY` 会使旧 Cookie 失效，重新登录；检查服务器时间是否准确 |
| 补货建议只有模板文案 | `LLM_API_KEY` 为空或不可用，属预期降级；填好 key 后 `docker compose up -d` 生效 |
| 镜像拉取 / 构建超慢 | 见第 3.1 节「国内网络优化」：配置 `registry-mirrors`；前端 Dockerfile 支持 `--build-arg NPM_REGISTRY=...`，后端支持 `--build-arg PIP_INDEX_URL=...`，另有 `BASE_IMAGE` / `NODE_IMAGE` / `NGINX_IMAGE` 可覆盖 |
| `docker: unknown command: docker compose` | 服务器缺少 Compose 插件（旧版 `docker-compose` v1 不能替代）：`sudo apt-get update && sudo apt-get install -y docker-compose-plugin`，或按第 3 节用官方源安装；验证 `docker compose version` |
| `docker compose pull` 长时间卡住（如 `Pulling` 数十分钟无进展） | GHCR 在中国大陆访问慢：先确认 `docker login ghcr.io` 已完成，再按 3.1 节配置加速/重试，或把镜像转存 ACR/SWR；**首选仍是 `pull`，源码构建（0.2 节）仅作最后手段** |
| clone 报 404 / `pull` 报 404 或镜像名不匹配 | 仓库地址必须为 `https://github.com/IceFireIcer/manage_system.git`；若误用 `IceFirerer` 等拼写会 clone 失败。compose 中的应用镜像必须为 `ghcr.io/icefireicer/labstock-api` / `ghcr.io/icefireicer/labstock-web`，用 `docker compose config \| grep "image: ghcr.io"` 核对 |
| `WARNING: Some service image(s) must be built from source` | 使用了含 `build:` 的旧版 compose 文件或在无镜像时直接 `up`：`git pull` 获取拆分后的 `docker-compose.yml`（先 `pull` 再 `up -d`）；需要构建时显式叠加 `-f docker-compose.build.yml` |
| 拉取镜像报 `denied` / `unauthorized` | GHCR 包为私有：`docker login ghcr.io -u <GitHub 用户名>`（PAT 需 `read:packages`），或在 GitHub Packages 将 `labstock-api` / `labstock-web` 设为 public |
| 磁盘占满 | `docker system df`；`docker image prune -af`；检查 `mysql_data` 增长与备份文件 |
| 时间显示为 UTC | 容器已设 `TZ=Asia/Shanghai`；确认宿主机时区 `timedatectl` |

抓取完整日志用于排查：

```bash
docker compose logs --no-color > /tmp/compose-logs.txt
docker inspect --format '{{json .State.Health}}' "$(docker compose ps -q backend)"
```

## 13. GitHub Actions 与 GHCR 镜像发布

仓库内置两条 GitHub Actions 工作流，推送到 GitHub 后自动运行，无需在服务器或平台侧搭建流水线。

### 13.1 CI（.github/workflows/ci.yml）

- 触发：`push`（所有分支）与 `pull_request`
- `backend` job：Python 3.12，`pip install -r backend/requirements.txt` → `pytest -q` → `ruff check .`
- `frontend` job：Node 22，`npm ci` → `npm run build`
- 同一 ref 的新推送会取消旧运行（`concurrency.cancel-in-progress`）

### 13.2 镜像发布（.github/workflows/docker-publish.yml）

- 触发：push 到 `main`、推送 `v*` 标签、`workflow_dispatch`
- 权限：`contents: read`、`packages: write`；用 `GITHUB_TOKEN` 登录 `ghcr.io`，无需额外 secret
- 构建上下文：`backend/` → `ghcr.io/icefireicer/labstock-api`；`frontend/` → `ghcr.io/icefireicer/labstock-web`
- 标签：`latest`（仅默认分支 `main`）、`sha-<short>`（每次提交）；推送 `v*` 时附加版本标签
- 使用 Buildx + GitHub Actions 缓存，重复构建更快

推送发布版本：

```bash
git tag v20261127-1
git push origin v20261127-1
```

发布后可在 GitHub 仓库的 Packages 页面查看 `labstock-api` / `labstock-web`。

### 13.3 GHCR 包可见性与服务器拉取

- GHCR 包默认私有：可在 GitHub Packages 将两个包设为 public；或使用带 `read:packages` 权限的 PAT 登录
- 服务器上登录（私有包）：

```bash
docker login ghcr.io -u <GitHub 用户名>
docker compose pull
docker compose up -d
```

- 升级与回滚都通过 `IMAGE_TAG` 完成：

```bash
IMAGE_TAG=sha-abc1234 docker compose pull
IMAGE_TAG=sha-abc1234 docker compose up -d
```

- 工作流在 GitHub 端执行，服务器只负责拉取与运行；构建失败时不会产生新标签，服务器继续运行上一个可用 `IMAGE_TAG`

### 13.4 手动与其他平台备用

`scripts/ci/` 保留三阶段脚本，可本地或任意 CI（Jenkins / GitLab CI 等）直接调用：

| 阶段 | 脚本 | 说明 |
| --- | --- | --- |
| 1 前端构建 | `scripts/ci/stage1-frontend-build.sh` | `npm ci` + `npm run build`，校验 `frontend/dist` |
| 2 后端测试 | `scripts/ci/stage2-backend-test.sh` | 创建/复用 venv，`pip install` + `pytest` + `ruff check .` |
| 3 镜像发布 | `scripts/ci/stage3-image-publish.sh` | 构建并推送 backend/web 镜像，默认仓库 `ghcr.io/icefireicer`，必需 `IMAGE_TAG` |

```bash
bash scripts/ci/stage1-frontend-build.sh
bash scripts/ci/stage2-backend-test.sh
IMAGE_TAG="$(date +%Y%m%d%H%M)" bash scripts/ci/stage3-image-publish.sh
```

`stage3` 缺少 `IMAGE_TAG` 时会输出用法并以非零码退出，避免误推镜像。脚本推送后仍需在服务器执行 `docker compose pull && docker compose up -d`。

## 14. 上线安全清单

- [ ] `.env` 权限 600，未提交到仓库（`git status` 确认无 `.env`）
- [ ] `docker compose version` 可用（Compose 插件已安装），`docker compose config --quiet` 校验通过且 backend/web 无 `build` 段
- [ ] `MYSQL_ROOT_PASSWORD` 与 `JWT_SECRET_KEY` 均为随机强值，未使用内置默认值
- [ ] 已从日志获取管理员初始密码并**立即修改**，新密码妥善保存
- [ ] 未将 `ADMIN_INIT_PASSWORD` 明文长期保留在 `.env` 或交付材料中
- [ ] 安全组仅放行 22 / 4900（或自定义端口），不暴露 3306 / 8000
- [ ] 配置数据库定期备份与磁盘快照
- [ ] GHCR 包可见性已确认：公开拉取，或服务器已用带 `read:packages` 的 PAT 登录
- [ ] GitHub Actions（`ci.yml`、`docker-publish.yml`）在 `main` 上通过，GHCR 存在可用 `sha-<short>` 或 `v*` 标签
- [ ] 建议接入 HTTPS：在前置 nginx/Caddy 或云负载均衡上终止 TLS（当前 Compose 默认 HTTP）
- [ ] 定期 `docker compose logs` 巡检与 `docker image prune` 清理