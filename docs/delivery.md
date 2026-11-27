# 交付说明

班级实训耗材智能管理系统 v1.1 交付说明，面向验收人、部署者与后续维护者，汇总系统能力、部署入口、配置项、管理员初始化、测试命令、目录结构与已知限制。

## 1. 项目简介

面向班级实训场景的耗材管理系统，解决领用无台账、库存不可见、补货靠经验、撤销无审计四类问题：

- 三角色权限：学生自助领用与查询，教师/班委代登记与撤销，班委维护档案与用户
- 批量领用：一次最多 5 行，整单原子扣减，任一行不足全部回滚，杜绝半截账
- 红冲撤销：不删除、不编辑原流水，回退库存并生成红冲流水，支持双向追溯
- 库存总览：低库存与缺货自动置顶，指标卡汇总总类数、预警数、今日领用
- 智能补货：规则引擎判定紧急/一般/观察三级，带来判断依据；大模型仅做润色，超时降级为规则模板
- 处置入库：建议页直接录入到货数量，原子加库存并留痕，形成补货闭环
- 统计审计：7/30 天净消耗（领用 − 红冲）、库存反推审计、操作日志全链路留痕、CSV 导出

技术栈：

| 层 | 选型 |
| -- | ---- |
| 后端 | Python 3.12、FastAPI 0.115、SQLAlchemy 2.0、Alembic、PyJWT、bcrypt |
| 前端 | Vue 3.5、Vite 5、Element Plus、Pinia、Axios、vue-router 4、ECharts 5 |
| 数据库 | MySQL 8.0（生产，Compose 内置）/ SQLite（本地零 Docker 开发兜底） |
| 部署 | Docker Compose（nginx + uvicorn + MySQL）；可选 CodeArts 流水线 |

## 2. 部署入口

完整部署手册见 **[deploy.md](deploy.md)**，包含部署拓扑、Docker 安装、密钥配置、构建启动、健康检查、日常运维、升级回滚、备份恢复、故障排查，以及可选 CodeArts 流水线三阶段配置。

快速命令：

```bash
cp .env.example .env
# 编辑 .env：MYSQL_ROOT_PASSWORD、JWT_SECRET_KEY 填随机强值
docker compose up -d --build
docker compose logs backend | grep bootstrap   # 获取管理员初始密码
curl -fsS http://127.0.0.1:4900/api/v1/health
```

云端资源（ECS、公网 IP、DeepSeek Key）需在部署前开通；本仓库不包含任何真实密钥。

## 3. 配置项

配置通过仓库根 `.env` 注入 Compose，模板见 `.env.example`：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `MYSQL_ROOT_PASSWORD` | 生产必填 | MySQL root 密码，建议只用字母数字 |
| `JWT_SECRET_KEY` | 生产必填 | JWT 签名密钥，至少 32 位随机字符 |
| `ADMIN_INIT_ACCOUNT` / `ADMIN_INIT_PASSWORD` / `ADMIN_INIT_NAME` | 否 | 首次创建的管理员账号/密码/姓名，默认 `admin` / 随机 / 系统管理员 |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | 否 | OpenAI 兼容接口，留空则智能补货只用规则引擎 |
| `WEB_PORT` | 否 | 对外 HTTP 端口，默认 4900 |
| `IMAGE_TAG` | 否 | 镜像版本标签，默认 latest |

内置默认密钥仅用于首次开箱启动；生产必须覆盖数据库密码与 JWT 密钥。

## 4. 管理员初始化

后端容器启动时依次执行 `alembic upgrade head` 与 `scripts/bootstrap_admin.py`：

- 不存在启用的管理员时，创建 `role=admin` 账号并启用；密码取 `ADMIN_INIT_PASSWORD`，未设置则随机生成并打印到后端日志
- 已存在启用的管理员时跳过，不覆盖密码；`ADMIN_INIT_PASSWORD` 仅对首次创建生效
- 系统不预置任何业务数据或账号

获取与修改初始密码的完整步骤见 [README「管理员初始化」](../README.md#管理员初始化)。当前版本无网页端自助改密接口，可通过后端容器内脚本更新密码哈希。

## 5. 本地开发与启动

### 5.1 一键启动（推荐）

```bash
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/dev-start.ps1

# Windows Git Bash / Linux / macOS
bash scripts/dev-start.sh
```

脚本自动创建虚拟环境、安装依赖、写入 SQLite 兜底配置、执行迁移、初始化管理员、启动后端与 Vite。访问 <http://127.0.0.1:5173>。

### 5.2 手动启动

```bash
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env                # 默认 SQLite 兜底配置
alembic upgrade head
python scripts/bootstrap_admin.py   # 首次初始化管理员，密码打印在终端
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

## 6. 目录结构

```text
manage_system/
├─ backend/
│  ├─ app/                 # FastAPI 应用（models、routers、agent、consumption）
│  ├─ alembic/versions/    # 数据库迁移（四张核心表）
│  ├─ tests/               # pytest 测试
│  ├─ scripts/
│  │  └─ bootstrap_admin.py# 首次部署管理员自举（幂等）
│  ├─ requirements.txt
│  ├─ Dockerfile
│  └─ alembic.ini
├─ frontend/
│  ├─ src/views/           # 总览/领用/记录/档案/用户/建议/处置/汇总/日志
│  ├─ Dockerfile
│  └─ nginx.conf
├─ docs/
│  ├─ deploy.md            # 云端部署手册
│  ├─ acceptance-checklist.md
│  ├─ delivery.md          # 本文档
│  ├─ adr/                 # 架构决策记录
│  └─ agents/              # 仓库协作说明
├─ scripts/                # 本地零 Docker 启动脚本、CI 三阶段脚本
├─ docker-compose.yml
└─ README.md
```

## 7. 测试与检查

```bash
# 后端全量测试
cd backend && pytest

# Lint
cd backend && ruff check .

# 前端生产构建
cd frontend && npm run build
```

当前基线：**238 passed**；`ruff check .` 通过；前端构建成功；真实 API 冒烟主线（登录 → 领用 → 撤销 → 建议 → 处置 → 统计 → 日志）通过。

## 8. 已知限制与用户待办

| 事项 | 说明 | 责任方 |
| ---- | ---- | ------ |
| 云端上线 | 需自有 Ubuntu 服务器、公网 IP、Docker 环境；按 deploy.md 执行 | 用户 |
| 管理员密码管理 | 无网页端自助改密接口；初始密码需首次登录后通过容器内脚本修改 | 用户 |
| DeepSeek Key | 未配置时智能补货自动降级为规则模板，功能不受影响 | 用户 |
| 安全库存阈值 | 初始无预置数据，上线前按实训室实际用量建档并校准 | 用户 |
| HTTPS / 域名 | SPEC 明确本期采用公网 IP + HTTP，不含证书配置 | 范围外 |
| 前端自动化测试 | SPEC 明确不写，由人工冒烟覆盖 | 范围外 |
| 多班级 / 采购 / 通知 | 见 SPEC「Out of Scope」 | 范围外 |
| 补录与审计口径 | `stock_after` 为执行时快照；补录按执行顺序（流水 id）入账，审计按执行顺序重建，展示时间仍为业务时间 | 已知限制 |
| 登录失败日志 | 登录失败会留痕，属合理安全增强（Spec 轴标为轻微越界），保留 | 已知限制 |

## 9. 技术债（本迭代明确不做）

以下为评审识别出的判断性重构项，收尾阶段执行会显著引入回归风险，保留现有行为与测试，记入后续迭代：

- `backend/app/routers/requisitions.py`（约 500 行）职责拆分：查询、登记、撤销、导出各自独立模块
- 前端 `Records.vue` / `Logs.vue` 的筛选与分页 composable 抽取
- `ROLE_NAMES` 等常量单源化、菜单/路由双源合并、枚举常量单源
- `OperationLog` 写入统一封装（当前各路由内联写日志，口径已一致）

## 10. 交付物清单

- 源代码：后端、前端、迁移、测试、启动脚本、Docker Compose、CI 三阶段脚本（`scripts/ci/`）
- 管理员自举脚本：`backend/scripts/bootstrap_admin.py`
- 验收清单：`docs/acceptance-checklist.md`
- 交付说明：`docs/delivery.md`
- 部署手册：`docs/deploy.md`
- 架构决策：`docs/adr/`（四表模型、处置即入库、红冲撤销、技术栈与部署）