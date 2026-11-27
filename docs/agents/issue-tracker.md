# Issue tracker: GitHub

Issues and specs for this repo live as GitHub issues in `IceFireIcer/manage_system`. Use the `gh` CLI for all operations; inside a clone `gh` infers the repository from `git remote -v`.

## Repository

- Host: https://github.com
- Web: https://github.com/IceFireIcer/manage_system
- Default branch: `main`
- CLI: `gh`（已登录，scopes 含 `repo`、`workflow`）

## Local proxy

本机访问 GitHub 需经本地代理，执行 `gh` 前先导出环境变量；仓库级 git 代理已用 `git config --local http.proxy` 配好：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
export HTTP_PROXY=http://127.0.0.1:7890
gh auth status
```

## Labels

五个 triage 角色标签在仓库中一次性创建，已存在时 `gh label create` 会报错，忽略即可：

```bash
gh label create needs-triage --color FBCA04 --description "Maintainer needs to evaluate this issue"
gh label create needs-info --color D876E3 --description "Waiting on reporter for more information"
gh label create ready-for-agent --color 0E8A16 --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --color 1D76DB --description "Requires human implementation"
gh label create wontfix --color E4E669 --description "Will not be actioned"
```

## Conventions

- **Create an issue**: `gh issue create --title "..." --body "..."`（多行正文用 heredoc）；需要标签时追加 `--label needs-triage`。
- **Read an issue**: `gh issue view <number> --json number,title,body,labels,comments`。
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`，配合 `--label`、`--state` 过滤。
- **Sub-issue**: `gh issue create --parent <parent> ...`，或 `gh issue edit <parent> --add-sub-issue <child>`（`gh` 2.94+）；旧版用 `gh api --method POST repos/IceFireIcer/manage_system/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`。不支持子议题时在子议题正文首行写 `Part of #<parent>`。
- **Blocking**: `gh issue create --blocked-by <n>,<n> ...` 或 `gh issue edit <child> --add-blocked-by <blocker>`（`gh` 2.94+）；旧版用 `gh api --method POST repos/IceFireIcer/manage_system/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`，其中 `<blocker-db-id>` 取 `gh api repos/IceFireIcer/manage_system/issues/<n> --jq .id`（数据库 id，不是 `#number`）。不可用时在正文首行写 `Blocked by: #<n>, #<n>`。所有 blocker 关闭后该议题才可开始。
- **Count open blockers**: `gh api repos/IceFireIcer/manage_system/issues/<n> --jq .issue_dependencies_summary.blocked_by`（仅统计未关闭的 blocker，0 表示可开始）；`gh issue view <n> --json blockedBy` 会列出已关闭的 blocker，需过滤：`--jq '[.blockedBy.nodes[] | select(.state == "OPEN")] | length'`。
- **Comment on an issue**: `gh issue comment <number> --body "..."`。
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`。
- **Close**: 先用 `gh issue comment <number> --body "..."` 说明原因，再 `gh issue close <number>`；对已关闭议题使用 `gh issue close --comment` 会丢弃评论但仍以 0 退出，避免这样用。

只向用户自己的仓库提 Issue（`IceFireIcer/manage_system`）。会话中首次 `gh issue create` 前用 `gh repo view --json nameWithOwner` 确认目标仓库。

## Pull requests as a triage surface

**PRs as a request surface: no.**

若改为 `yes`，PR 走与 Issue 相同的标签与状态，使用 `gh pr` 等价命令：

- **Read a PR**: `gh pr view <number>`，`gh pr view <number> --comments`（非 TTY 下只打印评论），`gh pr diff <number>`。
- **List external PRs for triage**: `gh api --paginate 'repos/IceFireIcer/manage_system/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`。
- **Comment / label / close**: `gh pr comment`、`gh pr edit --add-label`/`--remove-label`、`gh pr close`。

GitHub 的 Issue 与 PR 共用编号空间，裸 `#42` 可能是 PR：先用 `gh pr view 42` 解析，失败再 `gh issue view 42`。

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Read it as in **Read an issue** above.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

- **Map**: 单个带 `wayfinder:map` 标签的 Issue，承载 Notes / Decisions-so-far / Fog 正文：`gh issue create --label wayfinder:map`（标签不存在时先 `gh label create "wayfinder:map"`）。
- **Child ticket**: 通过子议题挂到 map 下（见 **Sub-issue**）；不支持子议题时在 map 正文用任务列表登记，并在子议题正文首行写 `Part of #<map>`。标签为 `wayfinder:<type>`（`research`/`prototype`/`grilling`/`task`）。认领后指派给执行者。
- **Blocking**: 见 Conventions 中的 **Blocking**。
- **Frontier query**: 列出 map 的未关闭子议题（`gh issue list --state open`，限定为 map 的子议题/任务列表），剔除有未关闭 blocker（见 **Count open blockers**，或正文 `Blocked by` 行仍指向未关闭议题）或已有 assignee 的项，按 map 顺序取第一个。
- **Claim**: `gh issue edit <n> --add-assignee @me`，作为会话的第一次写操作。
- **Resolve**: `gh issue comment <n> --body "<answer>"`，再 `gh issue close <n>`，最后把上下文指针追加到 map 的 Decisions-so-far。

## Fallback

`gh` 不可用（未登录、代理不通、API 限流）时，把 Issue 写成 `.scratch/<feature>/issue-<n>.md` 草稿，并提示用户在 GitHub 网页创建。