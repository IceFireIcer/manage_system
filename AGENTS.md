# AGENTS.md

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`IceFireIcer/manage_system`), read and written via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles map to default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
### 权限与路由约定

- 后端接口统一使用 `require_permission(<权限键>)`；仅初始化、角色矩阵等系统级动作使用 `require_super_admin`。不再新增 `require_role` 或按角色名白名单判权。
- 前端路由只声明 `meta.permission`，不写 `meta.roles`；路由守卫与侧边导航只依据有效权限集合。`super_admin` 的全量权限由后端 `/api/v1/permissions/me` 返回，前端不做角色特判。
- 有效权限按会话 `active_role` 计算，不做多角色并集；切换视角后由后端重新下发会话权限。
- 账号管理唯一入口为 `/settings/accounts`；`/api/v1/users` 仅保留为内部选择器、花名册导入等 API，不再提供独立页面入口。
- 角色键 `admin` 表示班委，班级范围以会话 `active_class` 为准，前端不得传 `class_id` 越权。