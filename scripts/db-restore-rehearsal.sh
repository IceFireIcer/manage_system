#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_FILE=""
MANIFEST_FILE=""
REPORT_DIR="${REPORT_DIR:-$ROOT/backups/rehearsals}"
REHEARSAL_IMAGE="${REHEARSAL_IMAGE:-mysql:8.0}"
REHEARSAL_PASSWORD="${REHEARSAL_PASSWORD:-rehearsal-only}"
DATABASE="${DATABASE:-manage_system}"
MAX_DATA_LOSS_MINUTES="${MAX_DATA_LOSS_MINUTES:-60}"
DRY_RUN=0

info() { printf '[restore-rehearsal] %s\n' "$*"; }
fail() {
  printf '[restore-rehearsal] 错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法: scripts/db-restore-rehearsal.sh --backup-file FILE [选项]

在一次性隔离 MySQL 容器中恢复指定备份，核对账号、台账、入库与操作日志，
记录恢复耗时与可接受丢失量并输出演练报告。全程不触碰正式库。

选项:
  --backup-file FILE              要演练的备份文件（必填）
  --manifest FILE                 备份清单（默认取同名 .manifest）
  --report-dir DIR                报告输出目录（默认 <仓库>/backups/rehearsals）
  --max-data-loss-minutes N       可接受丢失量阈值（分钟，默认 60）
  --database NAME                 数据库名（默认 manage_system）
  --dry-run                       只校验参数并打印计划，不启动容器
  -h, --help                      显示本帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup-file) BACKUP_FILE="$2"; shift 2 ;;
    --manifest) MANIFEST_FILE="$2"; shift 2 ;;
    --report-dir) REPORT_DIR="$2"; shift 2 ;;
    --max-data-loss-minutes) MAX_DATA_LOSS_MINUTES="$2"; shift 2 ;;
    --database) DATABASE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h | --help) usage; exit 0 ;;
    *) fail "未知参数：$1（使用 --help 查看用法）" ;;
  esac
done

[ -n "$BACKUP_FILE" ] || fail "必须通过 --backup-file 指定备份文件"
case "$MAX_DATA_LOSS_MINUTES" in
  '' | *[!0-9]*) fail "可接受丢失量阈值必须是正整数：$MAX_DATA_LOSS_MINUTES" ;;
esac

if [ -z "$MANIFEST_FILE" ]; then
  CANDIDATE="${BACKUP_FILE%.sql}.manifest"
  if [ -f "$CANDIDATE" ]; then
    MANIFEST_FILE="$CANDIDATE"
  fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
  info "dry-run：仅校验参数，不启动隔离容器"
  info "备份文件: $BACKUP_FILE"
  info "备份清单: ${MANIFEST_FILE:-<未提供，将跳过校验和>}"
  info "报告目录: $REPORT_DIR"
  info "数据库: $DATABASE"
  info "可接受丢失量阈值: $MAX_DATA_LOSS_MINUTES 分钟"
  info "计划: 启动一次性 $REHEARSAL_IMAGE 容器并恢复，核对 users/consumables/requisitions/operation_logs 行数"
  exit 0
fi

command -v docker >/dev/null 2>&1 || fail "未找到 docker"
[ -f "$BACKUP_FILE" ] || fail "找不到备份文件：$BACKUP_FILE"
[ -s "$BACKUP_FILE" ] || fail "备份文件为空：$BACKUP_FILE"

sha_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

manifest_get() {
  local key="$1" file="$2"
  [ -f "$file" ] || return 0
  grep -E "^${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

ACTUAL_SHA="$(sha_of "$BACKUP_FILE")"
EXPECTED_SHA=""
IMAGE_TAG="unknown"
BACKUP_MIGRATION="unknown"
BACKUP_AT=""
if [ -n "$MANIFEST_FILE" ] && [ -f "$MANIFEST_FILE" ]; then
  EXPECTED_SHA="$(manifest_get sha256 "$MANIFEST_FILE")"
  IMAGE_TAG="$(manifest_get image_tag "$MANIFEST_FILE")"
  BACKUP_MIGRATION="$(manifest_get migration_version "$MANIFEST_FILE")"
  BACKUP_AT="$(manifest_get created_at "$MANIFEST_FILE")"
  if [ -n "$EXPECTED_SHA" ] && [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
    fail "备份文件校验和不匹配：期望 $EXPECTED_SHA，实际 $ACTUAL_SHA"
  fi
fi
IMAGE_TAG="${IMAGE_TAG:-unknown}"
BACKUP_MIGRATION="${BACKUP_MIGRATION:-unknown}"

BACKUP_EPOCH="$(date -r "$BACKUP_FILE" +%s)"
NOW_EPOCH="$(date +%s)"
LOSS_MINUTES=$(( (NOW_EPOCH - BACKUP_EPOCH) / 60 ))
if [ "$LOSS_MINUTES" -le "$MAX_DATA_LOSS_MINUTES" ]; then
  LOSS_VERDICT="通过（$LOSS_MINUTES 分钟 <= 阈值 $MAX_DATA_LOSS_MINUTES 分钟）"
else
  LOSS_VERDICT="超限（$LOSS_MINUTES 分钟 > 阈值 $MAX_DATA_LOSS_MINUTES 分钟）"
fi

mkdir -p "$REPORT_DIR"
CID="$(docker run -d --rm --name "labstock-rehearsal-$$" \
  -e MYSQL_ROOT_PASSWORD="$REHEARSAL_PASSWORD" \
  -e MYSQL_DATABASE="$DATABASE" \
  "$REHEARSAL_IMAGE")"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

info "启动隔离 MySQL 容器 $CID，等待就绪 ..."
READY=0
for _ in $(seq 1 60); do
  if docker exec "$CID" mysqladmin ping -h 127.0.0.1 -uroot -p"$REHEARSAL_PASSWORD" --silent >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
[ "$READY" -eq 1 ] || fail "隔离 MySQL 容器 120 秒内未就绪"

info "恢复备份到隔离库 $DATABASE ..."
START_EPOCH="$(date +%s)"
if ! docker exec -i "$CID" mysql -uroot -p"$REHEARSAL_PASSWORD" "$DATABASE" <"$BACKUP_FILE"; then
  fail "恢复失败：$BACKUP_FILE"
fi
END_EPOCH="$(date +%s)"
DURATION_SECONDS=$((END_EPOCH - START_EPOCH))

count_rows() {
  docker exec "$CID" mysql -uroot -p"$REHEARSAL_PASSWORD" -N -B \
    -e "SELECT COUNT(*) FROM $1" "$DATABASE" 2>/dev/null | tr -d '\r'
}

USERS="$(count_rows users)"
ENABLED_USERS="$(count_rows "users WHERE status='enabled'")"
CONSUMABLES="$(count_rows consumables)"
REQUISITIONS="$(count_rows requisitions)"
RESTOCKS="$(count_rows "operation_logs WHERE action='restock'")"
LOGS="$(count_rows operation_logs)"
RESTORED_MIGRATION="$(docker exec "$CID" mysql -uroot -p"$REHEARSAL_PASSWORD" -N -B \
  -e "SELECT version_num FROM alembic_version LIMIT 1" "$DATABASE" 2>/dev/null | tr -d '\r')"
RESTORED_MIGRATION="${RESTORED_MIGRATION:-unknown}"

if [ "$BACKUP_MIGRATION" = "unknown" ]; then
  COMPAT_VERDICT="未知（备份清单未记录迁移版本）"
elif [ "$RESTORED_MIGRATION" = "$BACKUP_MIGRATION" ]; then
  COMPAT_VERDICT="一致（迁移版本 $RESTORED_MIGRATION）"
else
  COMPAT_VERDICT="不一致（清单 $BACKUP_MIGRATION，恢复后 $RESTORED_MIGRATION）"
fi

TS="$(date +%Y%m%d_%H%M%S)"
REPORT_FILE="$REPORT_DIR/restore-rehearsal_${TS}.md"
{
  printf '# 数据库恢复演练报告\n\n'
  printf -- '- 演练时间：%s\n' "$(date -Iseconds)"
  printf -- '- 备份文件：%s\n' "$BACKUP_FILE"
  printf -- '- 备份时间：%s\n' "${BACKUP_AT:-未知（按文件 mtime ${LOSS_MINUTES} 分钟前）}"
  printf -- '- 备份 sha256：%s\n' "$ACTUAL_SHA"
  printf -- '- 备份镜像标签：%s\n' "$IMAGE_TAG"
  printf -- '- 备份迁移版本：%s\n' "$BACKUP_MIGRATION"
  printf -- '- 恢复后迁移版本：%s\n' "$RESTORED_MIGRATION"
  printf -- '- 数据库兼容判定：%s\n' "$COMPAT_VERDICT"
  printf -- '- 恢复耗时：%s 秒\n' "$DURATION_SECONDS"
  printf -- '- 可接受丢失量：%s\n' "$LOSS_VERDICT"
  printf '\n## 核对结果\n\n'
  printf '| 核对项 | 数据表 | 行数 |\n'
  printf '|---|---|---:|\n'
  printf '| 账号 | users | %s |\n' "$USERS"
  printf '| 可登录账号 | users(status=enabled) | %s |\n' "$ENABLED_USERS"
  printf '| 台账 | consumables | %s |\n' "$CONSUMABLES"
  printf '| 领用流水 | requisitions | %s |\n' "$REQUISITIONS"
  printf '| 入库记录 | operation_logs(action=restock) | %s |\n' "$RESTOCKS"
  printf '| 操作日志 | operation_logs | %s |\n' "$LOGS"
  printf '\n## 结论\n\n'
  if [ "$ENABLED_USERS" -gt 0 ] && [ "$USERS" -gt 0 ]; then
    printf '隔离库已恢复出可登录（启用账号 %s 个）且可核对的系统；恢复耗时 %s 秒，可接受丢失量%s。\n' \
      "$ENABLED_USERS" "$DURATION_SECONDS" "$LOSS_VERDICT"
  else
    printf '警告：恢复后未发现可登录账号（users=%s，enabled=%s），请核查备份来源。\n' \
      "$USERS" "$ENABLED_USERS"
  fi
} >"$REPORT_FILE"

info "演练完成：$REPORT_FILE"
info "账号=$USERS（启用=$ENABLED_USERS）台账=$CONSUMABLES 领用=$REQUISITIONS 入库=$RESTOCKS 日志=$LOGS"
info "恢复耗时=$DURATION_SECONDS 秒，可接受丢失量：$LOSS_VERDICT"