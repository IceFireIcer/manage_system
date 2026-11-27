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