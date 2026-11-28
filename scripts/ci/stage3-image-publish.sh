#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMAGE_REGISTRY="${IMAGE_REGISTRY:-ghcr.io/icefireicer}"
IMAGE_TAG="${IMAGE_TAG:-}"

usage() {
  cat >&2 <<'EOF'
[stage3] 用法：
  IMAGE_TAG=<版本标签> bash scripts/ci/stage3-image-publish.sh

参数：
  IMAGE_REGISTRY  可选，镜像仓库前缀，默认 ghcr.io/icefireicer
                  推送镜像为 ${IMAGE_REGISTRY}/labstock-api 与 ${IMAGE_REGISTRY}/labstock-web
                  ghcr.io 需先 docker login ghcr.io（私有包用带 read:packages 的 GitHub PAT）
  IMAGE_TAG       必填，镜像版本标签，例如 v20261127-1（不要使用 latest 做回滚依据）

说明：GitHub Actions（.github/workflows/ci.yml 的 publish job）已在 main 与 v* 标签推送时原生构建并推送
      ghcr.io/icefireicer/labstock-api 与 ghcr.io/icefireicer/labstock-web；
      本脚本保留为手动发布或其他 CI 平台的备用路径。
EOF
}

if [ -z "$IMAGE_TAG" ]; then
  usage
  exit 2
fi

command -v docker >/dev/null 2>&1 || {
  printf '[stage3] 错误：未找到 docker 命令\n' >&2
  exit 1
}

BACKEND_IMAGE="${IMAGE_REGISTRY%/}/labstock-api:${IMAGE_TAG}"
WEB_IMAGE="${IMAGE_REGISTRY%/}/labstock-web:${IMAGE_TAG}"

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