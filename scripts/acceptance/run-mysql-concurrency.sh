#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/backend"
CONTAINER="labstock-concurrency-$$"
DATABASE="manage_system_concurrency"
MYSQL_ROOT_PASSWORD="concurrency-root"
MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.0}"
PORT="${MYSQL_TEST_PORT:-13306}"
KEEP=0
DRY_RUN=0

usage() {
  cat >&2 <<'EOF'
[concurrency] 用法：
  bash scripts/acceptance/run-mysql-concurrency.sh [--dry-run] [--keep] [--port <端口>]

说明：
  在隔离容器中启动 MySQL 8.0，运行 backend/tests/test_mysql_concurrency.py。
  未安装 docker 或无法启动容器时输出 SKIPPED 并以 0 退出；测试失败时非零退出。
  --dry-run  仅打印计划，不启动容器
  --keep     测试结束后保留容器，便于排查（需手动 docker rm -f）
EOF
}

info() { printf '[concurrency] %s\n' "$*"; }
skip() {
  printf '[concurrency] SKIPPED：%s\n' "$*"
  exit 0
}
fail() {
  printf '[concurrency] 错误：%s\n' "$*" >&2
  exit 1
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --keep) KEEP=1 ;;
    --port)
      shift
      [ "$#" -gt 0 ] || fail "--port 缺少端口值"
      PORT="$1"
      ;;
    --help | -h)
      usage
      exit 0
      ;;
    *) fail "未知参数：$1" ;;
  esac
  shift
done

case "$PORT" in
  '' | *[!0-9]*) fail "端口必须为数字：$PORT" ;;
esac
if [ "$PORT" -lt 1 ] || [ "$PORT" -gt 65535 ]; then
  fail "端口超出范围：$PORT"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  info "计划：启动 $MYSQL_IMAGE 容器 $CONTAINER，映射 127.0.0.1:$PORT，库 $DATABASE"
  info "然后运行 backend/tests/test_mysql_concurrency.py"
  info "容器在结束后删除（--keep 时保留）"
  exit 0
fi

command -v docker >/dev/null 2>&1 || skip "未找到 docker，无法启动真实 MySQL 并发验收"

cleanup() {
  if [ "$KEEP" -eq 0 ]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  fi
}
if [ "$KEEP" -eq 0 ]; then
  trap cleanup EXIT
fi

if ! docker run -d \
  --name "$CONTAINER" \
  -e MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PASSWORD" \
  -e MYSQL_ROOT_HOST=% \
  -e MYSQL_DATABASE="$DATABASE" \
  -p "127.0.0.1:${PORT}:3306" \
  "$MYSQL_IMAGE" >/dev/null 2>&1; then
  skip "无法启动 MySQL 容器（docker 或镜像不可用），请在有 MySQL 的环境执行"
fi

info "等待 MySQL 就绪..."
READY=0
for _ in $(seq 1 60); do
  if docker exec "$CONTAINER" mysqladmin ping \
    -h 127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --silent >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 2
done
if [ "$READY" -ne 1 ]; then
  if [ "$KEEP" -eq 0 ]; then
    skip "MySQL 容器在 120 秒内未就绪"
  fi
  fail "MySQL 容器在 120 秒内未就绪；容器 $CONTAINER 已保留"
fi

TEST_URL="mysql+pymysql://root:${MYSQL_ROOT_PASSWORD}@127.0.0.1:${PORT}/${DATABASE}?charset=utf8mb4"
info "运行真实 MySQL 并发用例..."
(
  cd "$BACKEND"
  LABSTOCK_MYSQL_TEST_URL="$TEST_URL" python -m pytest -q tests/test_mysql_concurrency.py
)

if [ "$KEEP" -eq 1 ]; then
  info "已保留容器 $CONTAINER；排查后可执行 docker rm -f $CONTAINER"
fi