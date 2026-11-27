#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-}"
IMAGE_TAG="${IMAGE_TAG:-}"

usage() {
  cat >&2 <<'EOF'
[stage3] 用法：
  IMAGE_REGISTRY=<镜像仓库地址> IMAGE_TAG=<版本标签> \
    bash scripts/ci/stage3-image-publish.sh

参数：
  IMAGE_REGISTRY  必填，镜像仓库前缀，例如 swr.cn-north-4.myhuaweicloud.com/<namespace>
  IMAGE_TAG       必填，镜像版本标签，例如 v20261127-1（不要使用 latest 做回滚依据）

前置：已执行 docker login 登录目标镜像仓库。
EOF
}

if [ -z "$IMAGE_REGISTRY" ] || [ -z "$IMAGE_TAG" ]; then
  usage
  exit 2
fi

command -v docker >/dev/null 2>&1 || {
  printf '[stage3] 错误：未找到 docker 命令\n' >&2
  exit 1
}

BACKEND_IMAGE="${IMAGE_REGISTRY%/}/manage-system-backend:${IMAGE_TAG}"
WEB_IMAGE="${IMAGE_REGISTRY%/}/manage-system-web:${IMAGE_TAG}"

info() { printf '[stage3] %s\n' "$*"; }

info "构建后端镜像 $BACKEND_IMAGE ..."
docker build -t "$BACKEND_IMAGE" "$ROOT/backend"

info "构建前端镜像 $WEB_IMAGE ..."
docker build -t "$WEB_IMAGE" "$ROOT/frontend"

info "推送后端镜像 ..."
docker push "$BACKEND_IMAGE"

info "推送前端镜像 ..."
docker push "$WEB_IMAGE"

info "镜像发布完成：$BACKEND_IMAGE"
info "镜像发布完成：$WEB_IMAGE"
info "服务器发布：IMAGE_TAG=$IMAGE_TAG docker compose pull && IMAGE_TAG=$IMAGE_TAG docker compose up -d --no-build"