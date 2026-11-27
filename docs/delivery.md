# 交付说明（T11）

班级实训耗材智能管理系统 v1.1 的最终交付包。本文档面向答辩评委、验收人与后续维护者，汇总系统能力、启动方式、演示数据、测试命令与已知限制。

## 1. 项目简介

面向班级实训场景的耗材管理系统，解决领用无台账、库存不可见、补货靠经验、撤销无审计四类问题：

- 三角色权限：学生自助领用与查询，教师/班委代登记与撤销，班委维护档案与用户
- 批量领用：一次最多 5 行，整单原子扣减，任一行不足全部回滚，杜绝半截账
- 红冲撤销：不删除、不编辑原流水，回退库存并生成红冲流水，支持双向追溯
- 库存总览：低库存与缺货自动置顶，指标卡汇总总类数、预警数、今日领用
- 智能补货：规则引擎判定紧急/一般/观察三级，带来判断依据；DeepSeek 仅做润色，超时降级为规则模板
- 处置入库：建议页直接录入到货数量，原子加库存并留痕，形成补货闭环
- 统计审计：7/30 天净消耗（领用 − 红冲）、库存反推审计、操作日志全链路留痕

技术栈：

| 层 | 选型 |
| -- | ---- |
| 后端 | Python 3.12、FastAPI 0.115、SQLAlchemy 2.0、Alembic、PyJWT、bcrypt |
| 前端 | Vue 3、Vite、Element Plus、Pinia、Axios、vue-router 4、ECharts 5 |
| 数据库 | 本地 SQLite（零 Docker 兜底）/ 生产 MySQL 8.0 |
| 部署 | Docker Compose（nginx + uvicorn + MySQL），CodeArts 流水线 |

## 2. 演示账号

运行 `alembic upgrade head` 后自动写入：

| 账号 | 密码 | 角色 | 姓名 |
| ---- | ---- | ---- | ---- |
| admin01 | Admin@123 | 班委 | 李班委 |
| teacher01 | Teacher@123 | 教师 | 王老师 |
| student01 | Student@123 | 学生 | 张同学 |

> 仅供演示与测试，生产环境请立即修改默认密码。

## 3. 本地启动

### 3.1 一键启动（推荐）

```bash
# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/dev-start.ps1

# Windows Git Bash / Linux / macOS
bash scripts/dev-start.sh
```

脚本自动创建虚拟环境、安装依赖、写入 SQLite 兜底配置、执行迁移、启动后端与 Vite。访问 <http://127.0.0.1:5173>。

### 3.2 手动启动

```bash
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows Git Bash
pip install -r requirements.txt
cp .env.example .env                # 默认 SQLite 兜底配置
alembic upgrade head
python scripts/seed_demo.py         # 初始化三级演示场景
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

## 4. 演示数据初始化

```bash
cd backend
python scripts/seed_demo.py
```

首次执行会写入 20 笔近 30 天历史领用，并把 5 类耗材校准为：

- 焊丝：1 卷 / 安全线 5 / Q7=8 → 紧急，建议补 12
- 记号笔：3 支 / 安全线 8 / Q7=7 → 一般，建议补 12
- 电阻：250 个 / 安全线 200 / Q7=100、Qprev7=40 → 观察（趋势），建议补 100
- 砂纸、实训导线：正常，不产生建议

脚本幂等：检测到 `[demo-seed]` 标记流水后直接跳过，不重复写入、不重复扣库存。建议在仅含预置数据的新库上首次执行。

演示步骤见 [demo-script.md](demo-script.md)，逐项验收见 [acceptance-checklist.md](acceptance-checklist.md)。

## 5. 云端部署入口

完整的 Ubuntu 云服务器 + Docker Compose 部署手册见 **[deploy.md](deploy.md)**，包含：

- 部署拓扑与安全组要求
- Docker 安装、`.env` 配置、镜像构建与启动
- 数据库迁移与种子数据
- 健康检查、常用运维、升级与镜像回滚、备份恢复、故障排查
- 可选：CodeArts 流水线三阶段配置

快速命令：

```bash
cp .env.example .env
docker compose up -d --build
curl -fsS http://127.0.0.1/api/v1/health
```

云端资源（ECS、公网 IP、DeepSeek Key）需由用户在部署前开通；本仓库不包含任何真实密钥。

## 6. 目录结构

```text
manage_system/
├─ backend/
│  ├─ app/                 # FastAPI 应用（models、routers、agent、consumption）
│  ├─ alembic/versions/    # 数据库迁移（建表、种子用户、种子耗材）
│  ├─ tests/               # pytest 232 项
│  ├─ scripts/seed_demo.py # 演示数据初始化（T11）
│  ├─ requirements.txt
│  └─ alembic.ini
├─ frontend/
│  └─ src/views/           # 总览/领用/记录/档案/用户/建议/汇总/日志
├─ docs/
│  ├─ deploy.md            # 云端部署手册
│  ├─ demo-script.md       # 3 分钟演示脚本（T11）
│  ├─ acceptance-checklist.md
│  ├─ delivery.md          # 本文档
│  ├─ adr/                 # 架构决策记录
│  └─ agents/              # 仓库协作说明
├─ scripts/                # 本地零 Docker 启动脚本
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

当前基线：**232 passed**；ruff 通过；前端构建成功；真实 API 冒烟主线（登录 → 领用 → 撤销 → 建议 → 处置 → 统计 → 日志）通过。

## 8. 已知限制与用户待办

| 事项 | 说明 | 责任方 |
| ---- | ---- | ------ |
| 演示视频 | 本仓库提供 3 分钟脚本与录制建议，视频需现场/自行录制 | 用户 |
| 云端上线 | 需自有 Ubuntu 服务器、域名/公网 IP、Docker 环境；按 deploy.md 执行 | 用户 |
| DeepSeek Key | 未配置时智能补货自动降级为规则模板，演示功能不受影响 | 用户 |
| 真实安全线 | 预置安全线为演示值，上线前按实训室实际用量校准 | 用户 |
| HTTPS / 域名 | SPEC 明确本期采用公网 IP + HTTP，不含证书配置 | 范围外 |
| 前端自动化测试 | SPEC 明确不写，由演示脚本人工冒烟覆盖 | 范围外 |
| 多班级 / 采购 / 通知 | 见 SPEC「Out of Scope」 | 范围外 |

## 9. 交付物清单

- 源代码：后端、前端、迁移、测试、启动脚本、Docker Compose
- 演示数据脚本：`backend/scripts/seed_demo.py`
- 演示脚本：`docs/demo-script.md`
- 验收清单：`docs/acceptance-checklist.md`
- 交付说明：`docs/delivery.md`
- 部署手册：`docs/deploy.md`
- 架构决策：`docs/adr/`（四表模型、处置即入库、红冲撤销、技术栈与部署）