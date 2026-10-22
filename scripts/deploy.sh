#!/usr/bin/env bash
# Manual deploy to the mini PC (same steps as .github/workflows/deploy-backend.yml).
set -euo pipefail
HOST="${DEPLOY_HOST:-100.67.129.25}"
USER_="${DEPLOY_USER:-ewgen15}"
ssh -i "${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}" "$USER_@$HOST" '
  set -e
  cd ~/whisper && git pull origin main
  cd backend && docker compose down && docker compose up -d --build
  sleep 3 && curl -fsS http://localhost:8000/health && echo
'
