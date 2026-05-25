#!/usr/bin/env bash
# Сборка образа на Mac (linux/amd64) и загрузка на VPS.
# Использование:
#   export VPS_HOST=root@150.251.152.5
#   export VPS_PORT=15259
#   export VPS_DIR=/opt/kaiten-agent
#   ./scripts/deploy-to-vps.sh
#
# Или через ~/.ssh/config Host vm-kaiten:
#   VPS_SSH=vm-kaiten VPS_DIR=/opt/kaiten-agent ./scripts/deploy-to-vps.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE_NAME="${IMAGE_NAME:-kaiten-agent-bot:latest}"
TAR_NAME="${TAR_NAME:-kaiten-agent-bot.tar.gz}"
VPS_HOST="${VPS_HOST:-}"
VPS_PORT="${VPS_PORT:-15259}"
VPS_DIR="${VPS_DIR:-/opt/kaiten-agent}"
VPS_SSH="${VPS_SSH:-}"

if [[ -n "$VPS_SSH" ]]; then
  SCP_TARGET="${VPS_SSH}:${VPS_DIR}/"
  SSH_TARGET="$VPS_SSH"
  SCP_EXTRA=()
  SSH_EXTRA=()
else
  : "${VPS_HOST:?Set VPS_HOST or VPS_SSH (e.g. root@150.251.152.5)}"
  SCP_TARGET="${VPS_HOST}:${VPS_DIR}/"
  SSH_TARGET="${VPS_HOST}"
  SCP_EXTRA=(-P "$VPS_PORT")
  SSH_EXTRA=(-p "$VPS_PORT")
fi

echo "==> Build ${IMAGE_NAME} (linux/amd64) from ${ROOT}"
docker build --platform linux/amd64 -t "$IMAGE_NAME" .

echo "==> Save image to ${TAR_NAME}"
docker save "$IMAGE_NAME" | gzip -9 > "$TAR_NAME"

echo "==> Upload to ${SCP_TARGET}"
scp "${SCP_EXTRA[@]}" "$TAR_NAME" docker-compose.vps.yml .env config/telegram_users.yaml "$SCP_TARGET"

echo "==> Load and restart on VPS"
ssh "${SSH_EXTRA[@]}" "$SSH_TARGET" bash -s <<EOF
set -euo pipefail
cd ${VPS_DIR}
docker load < ${TAR_NAME}
cp -f docker-compose.vps.yml docker-compose.yml
docker compose up -d --force-recreate --no-build --pull never
docker compose ps
docker compose logs --tail=30 kaiten-bot
EOF

echo "==> Done. Проверь в TG: ресёрч (шаги прогресса), /cancel, превью create."
