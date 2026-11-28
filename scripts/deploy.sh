#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${ROOT_DIR}/scripts/init-env.sh"

cd "$ROOT_DIR"
docker compose pull
docker compose up -d
docker compose ps

echo "[deploy] 完成；管理员初始密码：docker compose logs backend | grep bootstrap"