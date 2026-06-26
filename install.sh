#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_VER=3.10.15
DEPS=(make build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev
      libsqlite3-dev curl llvm libncursesw5-dev xz-utils tk-dev libxml2-dev
      libxmlsec1-dev libffi-dev liblzma-dev libncurses-dev git ffmpeg)

# This script is written for Debian/Ubuntu systems using apt.
if ! command -v apt >/dev/null 2>&1; then
  echo "This script requires apt (Debian/Ubuntu). Aborting."
  exit 1
fi

echo "[1/3] Installing system dependencies..."
sudo apt update
sudo apt install -y "${DEPS[@]}"
sudo apt install -y libdouble-conversion3 libxcb-cursor0 || true

echo "[2/3] Installing pyenv + Python $PY_VER (skip if already installed)..."
if [ ! -d "$HOME/.pyenv" ]; then
  git clone https://github.com/pyenv/pyenv.git "$HOME/.pyenv"
else
  echo "pyenv already exists, skipping clone"
fi

RC="$HOME/.bashrc"
if ! grep -q 'PYENV_ROOT' "$RC" 2>/dev/null; then
  echo "Appending pyenv initialization to $RC"
  cat >> "$RC" <<'EOF'

# pyenv
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"
EOF
else
  echo "pyenv init already present in $RC, skipping"
fi

export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init --path)"
eval "$(pyenv init -)"

pyenv install --skip-existing "$PY_VER"
pyenv global "$PY_VER"
echo "Python version: $(python --version 2>&1 || python3 --version)"

echo "[3/3] Installing project dependencies..."
python -m pip install --upgrade pip
if [ -f "$APP_DIR/requirements.txt" ]; then
  python -m pip install -r "$APP_DIR/requirements.txt"
else
  echo "requirements.txt not found in $APP_DIR, skipping dependency installation."
fi

echo ""
echo "Deployment complete."
echo "To start the project: cd $APP_DIR/src && python3 main.py"
