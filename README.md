# 班级实训耗材智能管理系统

前后端分离架构：

- `backend/`：Python 3.12 + FastAPI + pydantic v2 + uvicorn（单进程）
- `frontend/`：Vue 3 + Vite + Element Plus + Pinia + Axios + vue-router 4 + ECharts 5

## 环境要求

- Python 3.12+
- Node.js 18+（推荐 20+）
- MySQL 8.0（**本阶段 T01 暂不需要**，数据库将在后续票据接入）

## 后端本地启动

```bash
cd backend

# 1. 创建并激活虚拟环境
python -m venv venv
# Windows (Git Bash)
source venv/Scripts/activate
# Windows (CMD)
# venv\Scripts\activate.bat
# Windows (PowerShell)
# venv\Scripts\Activate.ps1

# 2. 安装依赖（如网络较慢可加国内镜像）
pip install -r requirements.txt
# pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 3. 准备环境变量（占位配置，不含真实密钥）
cp .env.example .env

# 4. 启动开发服务器
uvicorn app.main:app --reload
```

启动后访问健康检查：<http://127.0.0.1:8000/api/v1/health>，应返回 `200` 与 `{"status": "ok"}`。

## 前端本地启动

```bash
cd frontend
npm install
npm run dev
```

启动后访问 <http://localhost:5173> 查看首页。

## 本地零 Docker 一键启动（推荐）

云端故障时的兜底路径，不启动任何容器：

```bash
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/dev-start.ps1

# Windows Git Bash / Linux / macOS
bash scripts/dev-start.sh
```

脚本自动完成：创建 `backend/venv` → 安装依赖 → 无 `backend/.env` 时写入 SQLite 兜底配置 → `alembic upgrade head` → 后台启动 uvicorn → 安装前端依赖 → 启动 Vite。按 `Ctrl+C` 会同时停止前后端，后端日志在 `.dev-run/backend.log`。

- 默认数据库为 `backend/dev.db`（SQLite）；改用本机 MySQL 8.0 时修改 `backend/.env` 的 `DATABASE_URL`
- 访问 <http://127.0.0.1:5173>

## 云端部署（Docker Compose）

单台 Ubuntu 云服务器 + Docker Compose 部署（nginx 托管前端并反代 `/api`、uvicorn 运行后端、MySQL 8.0）：

```bash
cp .env.example .env    # 填 MYSQL_ROOT_PASSWORD、JWT_SECRET_KEY 等
docker compose config   # 静态校验
docker compose up -d --build
curl -fsS http://127.0.0.1/api/v1/health
```

后端容器启动时自动执行 `alembic upgrade head`（建表 + 演示数据）。完整步骤（安装 Docker、防火墙、迁移、验证、日志、升级、镜像回滚、备份与排错）见 **[docs/deploy.md](docs/deploy.md)**；CodeArts 流水线三阶段配置见该文档第 13 节（可选）。

## 测试与 Lint

```bash
# 后端单元测试
cd backend && pytest

# 后端代码检查
cd backend && ruff check .

# 数据库迁移（T02 起）
cd backend && alembic upgrade head
```

## 演示账号（T02 起）

运行 `alembic upgrade head` 后自动写入数据库（本地默认 SQLite `backend/dev.db`）：

| 账号 | 初始密码 | 角色 | 姓名 |
| ---- | -------- | ---- | ---- |
| teacher01 | Teacher@123 | teacher（教师） | 王老师 |
| admin01 | Admin@123 | admin（班委） | 李班委 |
| student01 | Student@123 | student（学生） | 张同学 |

> 仅供演示与测试，生产环境请立即修改密码。

## 说明

- 本地开发**零 Docker**：Python、Node、MySQL 均直接使用本机安装。
- T02 起本地默认使用 SQLite（`backend/.env` 中 `DATABASE_URL=sqlite:///./dev.db`）；接入 MySQL 8.0 时修改该变量即可。
- 认证采用 JWT 写入 httpOnly Cookie（SameSite=Lax，有效期 8 小时）。
- 被停用账号登录时统一返回 401「账号或密码错误」（不单独提示账号已停用，避免暴露账号状态）；停用仅影响登录，历史流水与操作日志保留可查。
- 配置通过 `pydantic-settings` 从 `backend/.env` 读取，`.env.example` 仅含占位符。