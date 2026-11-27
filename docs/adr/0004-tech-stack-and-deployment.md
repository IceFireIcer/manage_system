# 技术栈与部署形态

赛事明确要求依托华为云 CodeArts 完成代码托管、版本管理、CI/CD 流水线发布与项目协同，综合考察软件工程实践能力；开发一旦开始更换框架成本高。我们决定采用以下技术栈，并把 Docker 限制在云端、本地裸机开发：

- **后端**：Python 3.12 + FastAPI + pydantic v2 + uvicorn（单进程），依赖用 `requirements.txt` 冻结，配置用 pydantic-settings
- **数据访问**：SQLAlchemy 2.0（同步 Session）+ Alembic 迁移 + PyMySQL 驱动；建表由 Alembic 完成，演示数据由独立 seed 脚本注入
- **认证**：PyJWT + bcrypt（跳过 passlib），JWT 放 httpOnly Cookie（SameSite=Lax），同域 Nginx 反代无 CORS
- **前端**：Vue 3 + Vite + Element Plus + Pinia + Axios + vue-router 4 + ECharts 5（按需引入）
- **测试与质量**：pytest + httpx + pytest-cov + ruff（不引入 mypy）
- **LLM**：默认 DeepSeek API（OpenAI 兼容 SDK），经适配层保留切换华为云盘古的能力
- **部署**：云端单台华为云 ECS（2C4G）+ Docker Compose（nginx:alpine 托管静态前端并反代 /api、python:3.12-slim 运行 FastAPI、mysql:8.0 数据库），前端构建用 node:22-alpine；本地开发不启动 Docker，直接使用已装的 Python 3.12.7、MySQL 8.0.46 与 Node 25.4.0
- **CI/CD**：CodeArts 流水线三阶段——前端构建 → 后端 pytest → 镜像构建推送（SWR）并发布到 ECS；镜像带版本标签，回滚即切换标签

**Considered Options**: Flask + session（无自动 API 文档，不利于演示与联调）；Django 全家桶（与"轻量"定位冲突）；前端 CDN 版 Vue（无法体现流水线构建环节）；SQLModel 或裸 SQL（迁移/复杂查询生态弱）；异步 aiomysql（演示级并发用不上，复杂度翻倍）；localStorage 存 JWT（XSS 可窃取）；Naive UI / Ant Design Vue（中文文档与组件覆盖不占优）；mypy 严格类型门禁（课程项目成本高）；CCE 集群（成本与复杂度过度）；本地也装 Docker（Windows Desktop 实测 3.3GB + WSL2 磁盘 + 镜像约 5–6GB，收益仅为本地同构）；直接绑定盘古大模型（配额与接入链路的答辩风险高，故置于适配层之后）；全裸机（镜像发布这一最强的 DevOps 叙事丢失）。

**Consequences**: 技术栈锁定在 FastAPI/Vue/Docker（云端）上；本地与云端共用同一份 `requirements.txt` / `package.json`，仅启动方式不同；LLM 供应商可低成本切换；CodeArts 流水线必须包含前端构建、后端测试与镜像发布三个阶段；赛事"项目协同"与代码托管平台（当前 AtomGit）的合规性需向赛事方确认。