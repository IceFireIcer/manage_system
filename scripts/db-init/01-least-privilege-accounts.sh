#!/usr/bin/env bash
set -euo pipefail

APP_USER="${MYSQL_APP_USER:-appuser}"
APP_PASSWORD="${MYSQL_APP_PASSWORD:-}"
MIGRATE_USER="${MYSQL_MIGRATE_USER:-labstock_migrator}"
MIGRATE_PASSWORD="${MYSQL_MIGRATE_PASSWORD:-}"
BACKUP_USER="${MYSQL_BACKUP_USER:-labstock_backup}"
BACKUP_PASSWORD="${MYSQL_BACKUP_PASSWORD:-}"
DATABASE="${MYSQL_DATABASE:-manage_system}"
DRY_RUN=0

if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

info() { printf '[db-init] %s\n' "$*"; }
fail() {
  printf '[db-init] 错误：%s\n' "$*" >&2
  exit 1
}

validate_identifier() {
  local label="$1" value="$2"
  case "$value" in
    '' | *[!A-Za-z0-9_]*) fail "$label 只允许字母、数字与下划线：$value" ;;
  esac
}

sql_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\'/\\\'}"
  printf '%s' "$value"
}

validate_identifier "应用账号" "$APP_USER"
validate_identifier "迁移账号" "$MIGRATE_USER"
validate_identifier "备份账号" "$BACKUP_USER"
validate_identifier "数据库名" "$DATABASE"

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run：仅打印最小权限计划，不执行 SQL"
  info "应用账号: $APP_USER（SELECT/INSERT/UPDATE）"
  info "迁移账号: $MIGRATE_USER（DDL 与读写，用于 alembic）"
  info "备份账号: $BACKUP_USER（SELECT/LOCK TABLES/SHOW VIEW/EVENT/TRIGGER/PROCESS）"
  info "数据库: $DATABASE"
  info "不会删除或重建数据库，不触碰已有台账"
  exit 0
fi

for pair in \
  "MYSQL_ROOT_PASSWORD:${MYSQL_ROOT_PASSWORD:-}" \
  "MYSQL_APP_PASSWORD:$APP_PASSWORD" \
  "MYSQL_MIGRATE_PASSWORD:$MIGRATE_PASSWORD" \
  "MYSQL_BACKUP_PASSWORD:$BACKUP_PASSWORD"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  [ -n "$value" ] || fail "缺少 $name（环境变量或 Compose environment）"
done

APP_PASSWORD_SQL="$(sql_escape "$APP_PASSWORD")"
MIGRATE_PASSWORD_SQL="$(sql_escape "$MIGRATE_PASSWORD")"
BACKUP_PASSWORD_SQL="$(sql_escape "$BACKUP_PASSWORD")"

CLIENT_CONFIG="$(mktemp)"
trap 'rm -f "$CLIENT_CONFIG"' EXIT
{
  printf '[client]\n'
  printf 'user=root\n'
  printf 'password=%s\n' "$MYSQL_ROOT_PASSWORD"
} >"$CLIENT_CONFIG"
chmod 600 "$CLIENT_CONFIG"

info "配置最小权限账号（不删除、不重建数据库）"
mysql --defaults-extra-file="$CLIENT_CONFIG" <<SQL
CREATE USER IF NOT EXISTS '${APP_USER}'@'%' IDENTIFIED BY '${APP_PASSWORD_SQL}';
ALTER USER '${APP_USER}'@'%' IDENTIFIED BY '${APP_PASSWORD_SQL}';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${APP_USER}'@'%';
GRANT SELECT, INSERT, UPDATE ON \`${DATABASE}\`.* TO '${APP_USER}'@'%';

CREATE USER IF NOT EXISTS '${MIGRATE_USER}'@'%' IDENTIFIED BY '${MIGRATE_PASSWORD_SQL}';
ALTER USER '${MIGRATE_USER}'@'%' IDENTIFIED BY '${MIGRATE_PASSWORD_SQL}';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${MIGRATE_USER}'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX, REFERENCES,
      CREATE TEMPORARY TABLES, LOCK TABLES, CREATE VIEW, SHOW VIEW
      ON \`${DATABASE}\`.* TO '${MIGRATE_USER}'@'%';

CREATE USER IF NOT EXISTS '${BACKUP_USER}'@'%' IDENTIFIED BY '${BACKUP_PASSWORD_SQL}';
ALTER USER '${BACKUP_USER}'@'%' IDENTIFIED BY '${BACKUP_PASSWORD_SQL}';
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '${BACKUP_USER}'@'%';
GRANT SELECT, LOCK TABLES, SHOW VIEW, EVENT, TRIGGER ON \`${DATABASE}\`.* TO '${BACKUP_USER}'@'%';
GRANT PROCESS ON *.* TO '${BACKUP_USER}'@'%';

FLUSH PRIVILEGES;
SQL

info "账号配置完成：应用=$APP_USER，迁移=$MIGRATE_USER，备份=$BACKUP_USER"