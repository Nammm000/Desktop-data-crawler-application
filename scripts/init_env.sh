#!/usr/bin/env bash
# Create both virtualenvs, install pinned dependencies, and write
# backend/.env with freshly generated secrets when it does not exist yet.
# Idempotent: an existing backend/.env is never overwritten.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> backend venv"
python3 -m venv backend/.venv
backend/.venv/bin/pip install --quiet --upgrade pip
backend/.venv/bin/pip install --quiet -r backend/requirements.txt

echo "==> frontend venv"
python3 -m venv frontend/.venv
frontend/.venv/bin/pip install --quiet --upgrade pip
frontend/.venv/bin/pip install --quiet -r frontend/requirements.txt

if [ -f backend/.env ]; then
  if grep -q '^JWT_SECRET=CHANGE_ME' backend/.env; then
    echo "ERROR: backend/.env still has JWT_SECRET=CHANGE_ME — replace it with" >&2
    echo '  python3 -c "import secrets; print(secrets.token_urlsafe(48))"' >&2
    exit 1
  fi
  echo "==> backend/.env already exists (left untouched)"
  exit 0
fi

echo "==> writing backend/.env with fresh secrets"
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
# A Fernet key is urlsafe-base64 of 32 random bytes — no cryptography
# import needed to generate one.
FERNET_KEY=$(python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
sed -e "s|^JWT_SECRET=CHANGE_ME|JWT_SECRET=${JWT_SECRET}|" \
    -e "s|^CREDENTIALS_ENCRYPTION_KEY=CHANGE_ME|CREDENTIALS_ENCRYPTION_KEY=${FERNET_KEY}|" \
    backend/.env.example > backend/.env
echo "    JWT_SECRET and CREDENTIALS_ENCRYPTION_KEY generated."
echo "==> done. Start everything with: make up"
