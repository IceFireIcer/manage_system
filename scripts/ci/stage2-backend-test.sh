#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKEND="$ROOT/backend"
VENV_DIR="${VENV_DIR:-$BACKEND/.venv-ci}"
PYTHON="${PYTHON:-python3}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"

info() { printf '[stage2] %s\n' "$*"; }
fail() {
  printf '[stage2] 错误：%s\n' "$*" >&2
  exit 1
}

command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python
command -v "$PYTHON" >/dev/null 2>&1 || fail "未找到 python3/python，请先安装 Python 3.12+"

case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*) VENV_PY="$VENV_DIR/Scripts/python.exe" ;;
  *) VENV_PY="$VENV_DIR/bin/python" ;;
esac

if [ ! -x "$VENV_PY" ]; then
  info "创建虚拟环境 $VENV_DIR ..."
  "$PYTHON" -m venv "$VENV_DIR"
fi

info "安装后端依赖（index=$PIP_INDEX_URL）..."
"$VENV_PY" -m pip install -q -r "$BACKEND/requirements.txt" -i "$PIP_INDEX_URL"

info "执行 pytest ..."
(cd "$BACKEND" && "$VENV_PY" -m pytest -q)

info "执行 ruff check ..."
(cd "$BACKEND" && "$VENV_PY" -m ruff check .)

info "后端测试与静态检查通过"