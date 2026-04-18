#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

sudo apt update
sudo apt install -y \
  curl \
  git \
  nginx \
  nodejs \
  npm \
  python3 \
  python3-pip \
  python3-venv

if ! id -u uxauditor >/dev/null 2>&1; then
  sudo useradd --system --create-home --shell /bin/bash uxauditor
fi

cd "$PROJECT_DIR"

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install --with-deps chromium

sudo npm install -g vercel

mkdir -p shared/generated shared/output
sudo chown -R uxauditor:uxauditor "$PROJECT_DIR"

echo "Setup complete."
echo "Next:"
echo "1. Configure /etc/ux-ui-auditor.env"
echo "2. Install deploy/ux-ui-auditor.service"
echo "3. Configure Vercel with: vercel login && vercel link --yes"
