# Issue tracker: AtomGit

Issues and specs for this repo live in the AtomGit Issue tracker for `IceFire_Icer/manage_system` (project id `11491687`). Use the AtomGit v5 REST API with a personal access token.

## Repository

- Host: https://atomgit.com
- Web: https://atomgit.com/IceFire_Icer/manage_system
- API base: https://atomgit.com/api/v5
- Repo path: `/repos/IceFire_Icer/manage_system`
- Default branch: `main`

## Authentication

- Token via the `ATOMGIT_TOKEN` environment variable (persisted with `setx ATOMGIT_TOKEN`).
- Header: `PRIVATE-TOKEN: $ATOMGIT_TOKEN`
- Never write the token into the repo, issue bodies, comments, or logs. Rotate it if exposed.

## Verified endpoints (tested 2026-11-26)

| Operation | Method + path | Body / params |
|---|---|---|
| Who am I | `GET /user` | — |
| List issues | `GET /repos/{owner}/{repo}/issues?state=open\|closed\|all&limit=N&page=N` | — |
| Get issue | `GET /repos/{owner}/{repo}/issues/{number}` | — |
| Create issue | `POST /repos/{owner}/{repo}/issues` | JSON: `{"title": "...", "body": "..."}` |
| Update issue | `PATCH /repos/{owner}/{repo}/issues/{number}` | JSON: `{"title", "description", "labels", ...}` |
| Add comment | `POST /repos/{owner}/{repo}/issues/{number}/comments` | JSON: `{"body": "..."}` |
| List comments | `GET /repos/{owner}/{repo}/issues/{number}/comments` | — |
| Delete issue | `DELETE /repos/{owner}/{repo}/issues/{number}` | — |
| List labels | `GET /repos/{owner}/{repo}/labels?limit=50` | — |
| Create label | `POST /repos/{owner}/{repo}/labels?name=...&color=%23...` | **query/form params, not JSON** |

## Field quirks (important)

- Issue `number` is returned as a **string**.
- Create uses `body`; update (`PATCH`) uses `description`.
- `labels` in `PATCH` is a **comma-separated string of label names** (e.g. `"ready-for-agent"`), not an array. Sending `labels` during creation, or as an array, returns 400.
- Closing via API is **not working** (verified): `state_event: "close"` alone returns 400; with title/description it returns 200 but state stays `open`. Fallback: close in the AtomGit web UI, or record closure with the `wontfix` label plus a comment.
- Chinese content must be sent as UTF-8 JSON from Python (`urllib`/`requests`); shell `-d` on Windows Git Bash corrupts the encoding.
- `PATCH` accepts only: `title`, `description`, `assignee_ids`, `assignee_id`, `milestone_id`, `discussion_locked`, `labels`, `enterprise_labels`, `created_at`, `due_date`, `confidential`, `state_event`, `issue_category`, `issue_stage`, `issue_severity`, `pbi_id`.

## Labels

Created in the repo (IDs): `needs-triage` (13294895), `needs-info` (13294896), `ready-for-agent` (13294897), `ready-for-human` (13294898), `wontfix` (13294899).

## PRs as a triage surface

**PRs as a request surface: no.**

## When a skill says "publish to the issue tracker"

Create the issue via `POST /repos/.../issues` with title + body, then `PATCH` its `labels` with the comma-separated label name(s).

## When a skill says "fetch the relevant ticket"

`GET /repos/.../issues/{number}`, then `GET /repos/.../issues/{number}/comments` for the thread.

## Wayfinding fallback

Represent the map as an issue labelled `wayfinder:map`; each child is a separate issue whose body starts with `Part of #<map>` and records blocking as a `Blocked by: #<n>` line. The `wayfinder:map` label does not exist yet — create it via the label API when first used.

## Fallback

If the API is unreachable or no token is configured, write the issue as a markdown draft under `.scratch/<feature>/issue-<n>.md` and tell the user to paste it into the AtomGit web UI.

## Event recording

Record each triage operation against the issue via a comment (`POST .../comments`), since closing via API is unsupported: state transitions are expressed as labelled comments + label changes.