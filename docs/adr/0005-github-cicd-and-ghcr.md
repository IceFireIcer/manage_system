# 代码托管与 CI/CD 迁移到 GitHub + GHCR

用户决定全面转向 GitHub：远程仓库为 `https://github.com/IceFireIcer/manage_system.git`，代码托管、Issue 跟踪、CI/CD 与镜像分发统一由 GitHub 承载，原华为云 CodeArts 流水线与 AtomGit 不再作为主路径。我们决定：

- **代码托管与协作**：GitHub 仓库 `IceFireIcer/manage_system`，Issue 作为需求与缺陷的跟踪入口，标签沿用五角色 triage 体系，用 `gh` CLI 读写
- **CI**：GitHub Actions 工作流 `.github/workflows/ci.yml`，`push` 与 `pull_request` 触发，后端在 `backend/` 执行 `pytest -q` 与 `ruff check .`，前端在 `frontend/` 执行 `npm ci` 与 `npm run build`
- **镜像构建与发布**：`.github/workflows/docker-publish.yml` 在 push 到 `main`、推送 `v*` 标签与手动触发时，用 Buildx 构建并推送 `ghcr.io/icefireicer/labstock-api` 与 `ghcr.io/icefireicer/labstock-web`，标签为 `latest`（仅默认分支）、`sha-<short>`，版本标签推送时附加版本号
- **服务器部署**：`docker compose pull` 拉取 GHCR 镜像后启动；本地自行构建保留为 `--build` 备用路径

**Considered Options**: 保留 CodeArts 流水线 + SWR（需在平台内维护流水线与凭据，托管平台已切换）；继续依赖 AtomGit Issue API 自动化（关闭接口不可用，字段与编码坑多）；自建 GitLab 与 Runner（运维成本与项目规模不匹配）；只跑 CI 不发布镜像（服务器仍需本地构建，部署时间与构建依赖无法收敛）；Docker Hub 托管镜像（GHCR 与仓库权限、`GITHUB_TOKEN` 原生集成，无需额外 secret）。

**Consequences**: ADR-0004 中 CodeArts 流水线、SWR 镜像仓库与 AtomGit 托管的表述由本 ADR 取代，历史 ADR 保持原样；仓库默认分支须为 `main`，否则 `latest` 标签不会生成；GHCR 包默认私有，公开拉取需在 GitHub Packages 调整可见性，服务器拉取私有包需用带 `read:packages` 的 PAT 登录；`scripts/ci/stage3-image-publish.sh` 降级为手动或其他平台备用；本机访问 GitHub 需代理 `HTTPS_PROXY=http://127.0.0.1:7890`，仓库级 git 代理已配置。