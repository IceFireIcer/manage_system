#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
RUN_DIR="$ROOT/.dev-run"
HEALTH_URL="http://127.0.0.1:8000/api/v1/health"
PID_FILE="$RUN_DIR/backend.pid"
LOG_FILE="$RUN_DIR/backend.log"

mkdir -p "$RUN_DIR"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) VENV_PY="$BACKEND/venv/Scripts/python.exe" ;;
  *) VENV_PY="$BACKEND/venv/bin/python" ;;
esac

info() { printf '[dev-start] %s\n' "$*"; }
fail() {
  printf '[dev-start] 错误：%s\n' "$*" >&2
  exit 1
}

command -v python >/dev/null 2>&1 || fail "未找到 python，请先安装 Python 3.12+"
command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js 18+"
command -v curl >/dev/null 2>&1 || fail "未找到 curl"

if [ ! -x "$VENV_PY" ]; then
  info "创建后端虚拟环境 backend/venv ..."
  python -m venv "$BACKEND/venv"
fi

info "安装后端依赖 ..."
"$VENV_PY" -m pip install -q -r "$BACKEND/requirements.txt" \
  -i https://mirrors.aliyun.com/pypi/simple/

if [ ! -f "$BACKEND/.env" ]; then
  info "未找到 backend/.env，写入 SQLite 兜底配置（改用本机 MySQL 请自行修改）..."
  cat >"$BACKEND/.env" <<'EOF'
APP_ENV=dev
DEBUG=true
DATABASE_URL=sqlite:///./dev.db
JWT_SECRET_KEY=dev-only-change-me
LLM_API_KEY=
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
EOF
fi

info "执行数据库迁移（alembic upgrade head）..."
(cd "$BACKEND" && "$VENV_PY" -m alembic upgrade head)

info "初始化管理员账号（幂等；随机密码仅在首次创建时打印）..."
if ! (cd "$BACKEND" && "$VENV_PY" scripts/bootstrap_admin.py); then
  printf '[dev-start] 警告：管理员初始化失败（不影响服务启动），可稍后手动执行 backend/scripts/bootstrap_admin.py\n' >&2
fi

if [ -f "$PID_FILE" ]; then
  OLD_PID="$(cat "$PID_FILE")"
  if kill -0 "$OLD_PID" 2>/dev/null; then
    info "停止上次残留的后端进程 $OLD_PID"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
  fi
  rm -f "$PID_FILE"
fi

stop_backend() {
  if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
    if kill -0 "$PID" 2>/dev/null; then
      info "停止后端进程 $PID"
      kill "$PID" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  fi
}
trap stop_backend EXIT INT TERM

info "后台启动后端 uvicorn（日志：.dev-run/backend.log）..."
(
  cd "$BACKEND"
  nohup "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
    >"$LOG_FILE" 2>&1 &
  echo $! >"$PID_FILE"
)

info "等待后端健康检查 $HEALTH_URL ..."
READY=0
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    tail -n 30 "$LOG_FILE" >&2 || true
    fail "后端启动失败，请查看 .dev-run/backend.log"
  fi
  sleep 1
done
if [ "$READY" -ne 1 ]; then
  tail -n 30 "$LOG_FILE" >&2 || true
  fail "后端 30 秒内未通过健康检查"
fi
info "后端已就绪：$HEALTH_URL"

info "安装前端依赖 ..."
(cd "$FRONTEND" && npm install --no-audit --no-fund --registry=https://registry.npmmirror.com)

info "启动前端 Vite 开发服务器：http://127.0.0.1:5173（Ctrl+C 同时停止前后端）"
(cd "$FRONTEND" && npm run dev)