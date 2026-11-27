#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FRONTEND="$ROOT/frontend"
NPM_REGISTRY="${NPM_REGISTRY:-https://registry.npmmirror.com}"

info() { printf '[stage1] %s\n' "$*"; }
fail() {
  printf '[stage1] 错误：%s\n' "$*" >&2
  exit 1
}

command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js 22+"

info "安装前端依赖（registry=$NPM_REGISTRY）..."
cd "$FRONTEND"
npm ci --registry="$NPM_REGISTRY" --no-audit --no-fund

info "执行生产构建..."
npm run build

[ -d "$FRONTEND/dist" ] || fail "构建产物 frontend/dist 不存在"
info "前端构建完成：$FRONTEND/dist"