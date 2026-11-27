#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
SERVICE="${SERVICE:-db}"
DATABASE="${DATABASE:-manage_system}"
BACKUP_USER="${MYSQL_BACKUP_USER:-labstock_backup}"
DRY_RUN=0

info() { printf '[db-backup] %s\n' "$*"; }
fail() {
  printf '[db-backup] 错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法: scripts/db-backup.sh [选项]

对运行中的 Compose MySQL 服务执行一致性备份，生成备份文件与清单，
保存镜像标签与数据库迁移版本，按保留期清理旧备份，失败时以非零码退出。

选项:
  --backup-dir DIR       备份输出目录（默认 <仓库>/backups，可用 BACKUP_DIR 覆盖）
  --retention-days N     保留天数（默认 14，可用 RETENTION_DAYS 覆盖）
  --compose-file FILE    Compose 文件路径（默认 <仓库>/docker-compose.yml）
  --service NAME         MySQL 服务名（默认 db）
  --database NAME        数据库名（默认 manage_system）
  --dry-run              只校验参数并打印计划，不连接数据库
  -h, --help             显示本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup-dir) BACKUP_DIR="$2"; shift 2 ;;
    --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --database) DATABASE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "未知参数：$1（使用 --help 查看用法）" ;;
  esac
done

case "$RETENTION_DAYS" in
  '' | *[!0-9]*) fail "保留天数必须是正整数：$RETENTION_DAYS" ;;
esac

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run：仅校验参数，不连接数据库"
  info "Compose 文件: $COMPOSE_FILE"
  info "MySQL 服务: $SERVICE"
  info "数据库: $DATABASE"
  info "备份账号: $BACKUP_USER（最小权限：SELECT/LOCK TABLES/PROCESS）"
  info "备份目录: $BACKUP_DIR"
  info "独立副本目录: $BACKUP_DIR/copies"
  info "保留天数: $RETENTION_DAYS"
  info "将生成: $BACKUP_DIR/${DATABASE}_<时间戳>.sql"
  info "将生成: $BACKUP_DIR/${DATABASE}_<时间戳>.manifest（含镜像标签、迁移版本、校验和）"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "未找到 docker"
docker compose version >/dev/null 2>&1 || fail "未找到 docker compose 插件"
[ -f "$COMPOSE_FILE" ] || fail "找不到 Compose 文件：$COMPOSE_FILE"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

read_env() {
  local key="$1" file="$ROOT/.env"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

if [ -z "${MYSQL_BACKUP_USER:-}" ]; then
  MYSQL_BACKUP_USER="$(read_env MYSQL_BACKUP_USER)"
fi
BACKUP_USER="${MYSQL_BACKUP_USER:-$BACKUP_USER}"
if [ -z "${MYSQL_BACKUP_PASSWORD:-}" ]; then
  MYSQL_BACKUP_PASSWORD="$(read_env MYSQL_BACKUP_PASSWORD)"
fi
[ -n "${MYSQL_BACKUP_PASSWORD:-}" ] || fail "缺少 MYSQL_BACKUP_PASSWORD（环境变量或 .env）"

if [ -z "${IMAGE_TAG:-}" ]; then
  IMAGE_TAG="$(read_env IMAGE_TAG)"
fi
IMAGE_TAG="${IMAGE_TAG:-latest}"

mkdir -p "$BACKUP_DIR" "$BACKUP_DIR/copies"
trap 'printf "[db-backup] 失败时间 %s，详见上一条错误\n" "$(date -Iseconds)" >>"$BACKUP_DIR/backup-failed.log" 2>/dev/null || true' ERR

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/${DATABASE}_${TS}.sql"
MANIFEST_FILE="$BACKUP_DIR/${DATABASE}_${TS}.manifest"

MIGRATION_VERSION="$(compose exec -T "$SERVICE" env MYSQL_PWD="$MYSQL_BACKUP_PASSWORD" \
  mysql -u"$BACKUP_USER" -N -B \
  -e "SELECT version_num FROM alembic_version LIMIT 1" "$DATABASE" 2>/dev/null | tr -d '\r' || true)"
MIGRATION_VERSION="${MIGRATION_VERSION:-unknown}"

info "开始备份数据库 $DATABASE -> $BACKUP_FILE"
if ! compose exec -T "$SERVICE" env MYSQL_PWD="$MYSQL_BACKUP_PASSWORD" \
  mysqldump -u"$BACKUP_USER" \
  --single-transaction --routines --triggers --databases "$DATABASE" >"$BACKUP_FILE"; then
  rm -f "$BACKUP_FILE"
  printf '[db-backup] 失败时间 %s：mysqldump 执行失败\n' "$(date -Iseconds)" >>"$BACKUP_DIR/backup-failed.log"
  fail "mysqldump 执行失败，未生成备份"
fi
if [ ! -s "$BACKUP_FILE" ]; then
  rm -f "$BACKUP_FILE"
  fail "备份文件为空，已删除"
fi

if command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(sha256sum "$BACKUP_FILE" | awk '{print $1}')"
else
  SHA256="$(shasum -a 256 "$BACKUP_FILE" | awk '{print $1}')"
fi
SIZE_BYTES="$(wc -c <"$BACKUP_FILE" | tr -d ' ')"

{
  printf 'backup_file=%s\n' "$(basename "$BACKUP_FILE")"
  printf 'created_at=%s\n' "$(date -Iseconds)"
  printf 'database=%s\n' "$DATABASE"
  printf 'image_tag=%s\n' "$IMAGE_TAG"
  printf 'migration_version=%s\n' "$MIGRATION_VERSION"
  printf 'size_bytes=%s\n' "$SIZE_BYTES"
  printf 'sha256=%s\n' "$SHA256"
  printf 'retention_days=%s\n' "$RETENTION_DAYS"
} >"$MANIFEST_FILE"

cp -f "$BACKUP_FILE" "$BACKUP_DIR/copies/"
cp -f "$MANIFEST_FILE" "$BACKUP_DIR/copies/"

if [ "$RETENTION_DAYS" -gt 0 ]; then
  find "$BACKUP_DIR" -maxdepth 1 -type f -name "${DATABASE}_*.sql" -mtime +"$RETENTION_DAYS" -print -delete || true
  find "$BACKUP_DIR" -maxdepth 1 -type f -name "${DATABASE}_*.manifest" -mtime +"$RETENTION_DAYS" -print -delete || true
  find "$BACKUP_DIR/copies" -maxdepth 1 -type f -name "${DATABASE}_*" -mtime +"$RETENTION_DAYS" -print -delete || true
fi

info "备份完成：$BACKUP_FILE（$SIZE_BYTES 字节，sha256=$SHA256）"
info "清单文件：$MANIFEST_FILE（镜像=$IMAGE_TAG，迁移版本=$MIGRATION_VERSION）"
info "独立副本：$BACKUP_DIR/copies/（请另行同步到异机或对象存储）"