#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${1:-${ENV_FILE:-${ROOT_DIR}/.env}}"
EXAMPLE_FILE="${EXAMPLE_FILE:-${ROOT_DIR}/.env.example}"

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ ! -f "$EXAMPLE_FILE" ]]; then
    echo "[init-env] 缺少模板文件：${EXAMPLE_FILE}" >&2
    exit 1
  fi
  cp "$EXAMPLE_FILE" "$ENV_FILE"
  echo "[init-env] 已从模板创建 ${ENV_FILE}"
fi

if ! command -v openssl >/dev/null 2>&1; then
  echo "[init-env] 未找到 openssl，请先安装：apt-get install -y openssl" >&2
  exit 1
fi

random_hex() {
  openssl rand -hex "$1"
}

is_placeholder() {
  local value="$1"
  [[ -z "$value" || "$value" == *"change-me"* || "$value" == "user:password" ]]
}

set_if_placeholder() {
  local key="$1"
  local value="$2"
  local current
  current="$(grep -E "^${key}=" "$ENV_FILE" | tail -n1 | cut -d= -f2- | tr -d '\r' || true)"
  if is_placeholder "$current"; then
    if grep -qE "^${key}=" "$ENV_FILE"; then
      sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
      if [[ -n "$(tail -c 1 "$ENV_FILE")" ]]; then
        printf '\n' >> "$ENV_FILE"
      fi
      printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
    echo "[init-env] 已生成 ${key}"
  else
    echo "[init-env] ${key} 已配置，保留"
  fi
}

set_if_placeholder MYSQL_ROOT_PASSWORD "$(random_hex 16)"
set_if_placeholder MYSQL_APP_PASSWORD "$(random_hex 16)"
set_if_placeholder MYSQL_MIGRATE_PASSWORD "$(random_hex 16)"
set_if_placeholder MYSQL_BACKUP_PASSWORD "$(random_hex 16)"
set_if_placeholder JWT_SECRET_KEY "$(random_hex 32)"
set_if_placeholder SETTINGS_ENCRYPTION_KEY "$(random_hex 32)"

sed -i 's/\r$//' "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "[init-env] 密钥已就绪：${ENV_FILE}"
echo "[init-env] 提示：数据库数据卷初始化后请勿重新生成这些值，否则后端将无法连接已有数据库。"