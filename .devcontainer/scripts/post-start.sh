#!/usr/bin/bash

set -euo pipefail

WORKSPACE="$1"

git config --global --add safe.directory "$WORKSPACE"
echo "✅ Added $WORKSPACE to git safe directories"

if [[ -z "${CODESPACES:-}" ]] && [[ -z "${GITHUB_ACTIONS:-}" ]]; then
  SOCK_PATH=$(find /tmp -maxdepth 1 -name "vscode-ssh-auth-*.sock" 2>/dev/null | head -n1 || true)
  if [[ -n "$SOCK_PATH" ]]; then
    echo "export SSH_AUTH_SOCK=$SOCK_PATH" >> ~/.zshrc
    echo "export SSH_AUTH_SOCK=$SOCK_PATH" >> ~/.bashrc
    export SSH_AUTH_SOCK=$SOCK_PATH
    echo "✅ Mapped SSH_AUTH_SOCK"
  else
    echo "⚠️  VS Code agent socket not found; leaving SSH_AUTH_SOCK unchanged"
  fi
else
  echo "⏩ Skipping SSH_AUTH_SOCK setup for Codespaces or GitHub Actions"
fi

# Local PostgreSQL is the simulator: start it and make sure the sqrtl
# database exists (it is created at image build time, but the cluster can be
# reset by a rebuild of the data volume).
sudo service postgresql start
if ! psql -d sqrtl -Atc 'SELECT 1' >/dev/null 2>&1; then
  sudo -u postgres createuser --superuser dev 2>/dev/null || true
  sudo -u postgres createdb --owner dev sqrtl
fi
echo "✅ PostgreSQL is up (postgresql://localhost/sqrtl)"

pip install --quiet --user -e "$WORKSPACE[test]"
echo "✅ Installed sqrtl in editable mode ($(sqrtl --version))"

echo '✅ Initialized development environment...'
