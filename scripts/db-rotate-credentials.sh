#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
SERVICE="${SERVICE:-db}"
BACKEND_SERVICE="${BACKEND_SERVICE:-backend}"
TARGET="${TARGET:-all}"
DRY_RUN=0

info() { printf '[db-rotate] %s\n' "$*"; }
fail() {
  printf '[db-rotate] 错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法: scripts/db-rotate-credentials.sh [选项]

轮换应用、迁移或备份数据库账号密码，保留现有数据库与全部台账数据。
轮换后重建后端容器使新凭据生效，并以受控读查询验证台账仍可访问。

选项:
  --target app|migrate|backup|all   轮换目标（默认 all）
  --env-file FILE                   配置文件（默认 <仓库>/.env）
  --compose-file FILE               Compose 文件（默认 <仓库>/docker-compose.yml）
  --service NAME                    MySQL 服务名（默认 db）
  --backend-service NAME            后端服务名（默认 backend）
  --dry-run                         只打印计划，不改密码、不重启容器
  -h, --help                        显示本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --backend-service) BACKEND_SERVICE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "未知参数：$1（使用 --help 查看用法）" ;;
  esac
done

case "$TARGET" in
  app | migrate | backup | all) ;;
  *) fail "轮换目标必须是 app、migrate、backup 或 all：$TARGET" ;;
esac

read_env() {
  local key="$1" file="$ENV_FILE"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

env_value() {
  local name="$1" value="${!1:-}"
  if [ -z "$value" ]; then
    value="$(read_env "$name")"
  fi
  printf '%s' "$value"
}

APP_USER="${MYSQL_APP_USER:-$(read_env MYSQL_APP_USER)}"
MIGRATE_USER="${MYSQL_MIGRATE_USER:-$(read_env MYSQL_MIGRATE_USER)}"
BACKUP_USER="${MYSQL_BACKUP_USER:-$(read_env MYSQL_BACKUP_USER)}"
APP_USER="${APP_USER:-appuser}"
MIGRATE_USER="${MIGRATE_USER:-labstock_migrator}"
BACKUP_USER="${BACKUP_USER:-labstock_backup}"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run：仅打印凭据轮换计划，不修改数据库与 .env"
  info "目标: $TARGET"
  info "配置文件: $ENV_FILE"
  info "Compose 文件: $COMPOSE_FILE"
  info "将执行 ALTER USER 轮换：应用=$APP_USER，迁移=$MIGRATE_USER，备份=$BACKUP_USER"
  info "不会执行 down -v，不删除数据卷，不重建或清空数据库"
  info "轮换后重建 $BACKEND_SERVICE 容器，并核对 consumables 台账仍可读"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "未找到 docker"
docker compose version >/dev/null 2>&1 || fail "未找到 docker compose 插件"
docker compose -f "$COMPOSE_FILE" version >/dev/null 2>&1 || fail "Compose 文件不可用：$COMPOSE_FILE"
[ -f "$ENV_FILE" ] || fail "找不到配置文件：$ENV_FILE"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

ROOT_PASSWORD="$(env_value MYSQL_ROOT_PASSWORD)"
[ -n "$ROOT_PASSWORD" ] || fail "缺少 MYSQL_ROOT_PASSWORD（环境变量或 $ENV_FILE）"

sql_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\\\'}"
  printf '%s' "$value"
}

new_password() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 16
  else
    od -An -N16 -tx1 /dev/urandom | tr -d ' \n'
  fi
}

mysql_root() {
  compose exec -T "$SERVICE" env MYSQL_PWD="$ROOT_PASSWORD" mysql -uroot -N -B -e "$1"
}

alter_user() {
  local user="$1" password="$2"
  mysql_root "ALTER USER '${user}'@'%' IDENTIFIED BY '$(sql_escape "$password")'; FLUSH PRIVILEGES;"
}

update_env() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" >"$tmp"
  else
    cp "$ENV_FILE" "$tmp"
    printf '%s=%s\n' "$key" "$value" >>"$tmp"
  fi
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
}

should_rotate() {
  case "$TARGET" in
    all) return 0 ;;
    "$1") return 0 ;;
    *) return 1 ;;
  esac
}

cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d%H%M%S)"
chmod 600 "${ENV_FILE}.bak."* 2>/dev/null || true

RESTART_NEEDED=0

if should_rotate app; then
  NEW_APP_PASSWORD="$(new_password)"
  alter_user "$APP_USER" "$NEW_APP_PASSWORD"
  update_env MYSQL_APP_PASSWORD "$NEW_APP_PASSWORD"
  RESTART_NEEDED=1
  info "已轮换应用账号 $APP_USER"
fi

if should_rotate migrate; then
  NEW_MIGRATE_PASSWORD="$(new_password)"
  alter_user "$MIGRATE_USER" "$NEW_MIGRATE_PASSWORD"
  update_env MYSQL_MIGRATE_PASSWORD "$NEW_MIGRATE_PASSWORD"
  RESTART_NEEDED=1
  info "已轮换迁移账号 $MIGRATE_USER"
fi

if should_rotate backup; then
  NEW_BACKUP_PASSWORD="$(new_password)"
  alter_user "$BACKUP_USER" "$NEW_BACKUP_PASSWORD"
  update_env MYSQL_BACKUP_PASSWORD "$NEW_BACKUP_PASSWORD"
  info "已轮换备份账号 $BACKUP_USER"
fi

if [ "$RESTART_NEEDED" -eq 1 ]; then
  info "重建后端容器以加载新凭据（不触碰数据库数据卷）"
  compose up -d "$BACKEND_SERVICE"
fi

info "验证应用账号连接与既有台账读取"
compose exec -T "$BACKEND_SERVICE" python - <<'PY'
import os

from sqlalchemy import create_engine, text

engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
with engine.connect() as connection:
    count = connection.execute(text("SELECT COUNT(*) FROM consumables")).scalar()
print(f"[db-rotate] app-account-ok consumables={count}")
PY

info "完成：现有数据库与台账保留，旧密码已失效"