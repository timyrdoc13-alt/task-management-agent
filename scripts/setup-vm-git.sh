#!/usr/bin/env bash
# Run on VM (VNC console) after git clone. Usage:
#   cd /opt/kaiten-agent && ./scripts/setup-vm-git.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo "Create .env first (copy from Mac). Example: nano .env"
  exit 1
fi
if ! command -v docker >/dev/null; then
  apt-get update
  apt-get install -y docker.io docker-compose-plugin git
  systemctl enable --now docker
fi
cp -f docker-compose.vps.yml docker-compose.yml
docker compose build
docker compose up -d --force-recreate
docker compose logs --tail=30 kaiten-bot
