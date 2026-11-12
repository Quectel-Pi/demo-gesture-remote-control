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

# Lightweight "still running" indicator for long downloads/builds.
# Streams the wrapped command's output untouched and preserves its exit code.
# Prints a heartbeat line roughly every 15 s so the user knows it is not stuck.
heartbeat() {
  local label="$1"; shift
  local start rc
  echo "==> $label"
  "$@" &
  local pid=$!
  start=$SECONDS
  while kill -0 "$pid" 2>/dev/null; do
    sleep 1
    if kill -0 "$pid" 2>/dev/null && [ $(((SECONDS - start) % 15)) -eq 0 ]; then
      echo "  [$(date +%H:%M:%S)] still running... $((SECONDS - start)) s elapsed"
    fi
  done
  if wait "$pid"; then
    rc=0
  else
    rc=$?
  fi
  if [ "$rc" -eq 0 ]; then
    echo "  done in $((SECONDS - start)) s"
  else
    echo "  FAILED with exit code $rc after $((SECONDS - start)) s"
  fi
  return "$rc"
}


# [1/5] Optional relocation: keep bulky $HOME dirs on the /data partition when the
#       root filesystem is small (Quectel Pi fullstack images ship an 8G rootfs
#       that is nearly full out of the box). Skips silently on boards without a
#       usable separate /data (e.g. SD-card boot).
relocate_to_data() {
  local src="$1" name owner data_root root_dev data_dev avail
  name="$(basename "$src")"
  [ -d /data ] || return 0
  mountpoint -q /data || return 0
  root_dev="$(df -P / 2>/dev/null | awk 'NR==2{print $1}')"
  data_dev="$(df -P /data 2>/dev/null | awk 'NR==2{print $1}')"
  [ -n "$data_dev" ] && [ "$data_dev" != "$root_dev" ] || return 0
  avail="$(df -P /data 2>/dev/null | awk 'NR==2{print $4}')"
  [ "${avail:-0}" -ge 1048576 ] || return 0          # /data must have >= 1G free
  # Target dir is named after the owner of $HOME, NOT a hardcoded "pi":
  # factory images -> pi keeps /data/pi (backward compatible); any other
  # username (or root with HOME=/home/pi) gets its own /data/<owner>.
  owner="$(stat -c '%U' "$HOME" 2>/dev/null || id -un 2>/dev/null || echo root)"
  data_root="/data/$owner"
  if [ ! -w "$data_root" ]; then                      # first run: create + own it
    sudo mkdir -p "$data_root" || return 0
    sudo chown "$owner:$owner" "$data_root" || return 0
  fi
  [ -w "$data_root" ] || return 0
  if [ -L "$src" ]; then
    return 0                                          # already relocated
  elif [ -e "$src" ]; then
    echo "  rootfs space is limited: moving $src -> $data_root/$name"
    if mv "$src" "$data_root/$name"; then
      if ln -s "$data_root/$name" "$src"; then
        echo "  done. $src now lives on the /data partition."
      else
        echo "  WARNING: symlink failed, restoring original location"
        mv "$data_root/$name" "$src" || true
      fi
    else
      echo "  WARNING: move failed, keeping $src on the root filesystem"
    fi
  else                                                # fresh board: pre-create
    mkdir -p "$data_root/$name" && ln -s "$data_root/$name" "$src"
  fi
  return 0
}

echo "[1/5] Checking for a roomy /data partition..."
relocate_to_data "$HOME/.pyenv"
relocate_to_data "$HOME/.cache"

echo "[2/5] Installing system dependencies..."
sudo apt update
sudo apt install -y "${DEPS[@]}"
sudo apt install -y libdouble-conversion3 libxcb-cursor0 || true

echo "[3/5] Installing pyenv + Python $PY_VER (skip if already installed)..."
if [ ! -x "$HOME/.pyenv/bin/pyenv" ]; then
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

heartbeat "Downloading & building Python $PY_VER (first install may take 10-30 min)" pyenv install --skip-existing "$PY_VER"
pyenv global "$PY_VER"
echo "Python version: $(python --version 2>&1 || python3 --version)"

echo "[4/5] Installing project dependencies..."
python -m pip install --upgrade pip
if [ -f "$APP_DIR/requirements.txt" ]; then
  heartbeat "Downloading & installing project dependencies" python -m pip install -r "$APP_DIR/requirements.txt"
else
  echo "ERROR: requirements.txt not found in $APP_DIR" >&2
  exit 1
fi

echo "[5/5] Configuring platform-specific inference dependencies..."
USE_RK3576_NPU="$(PYTHONPATH="$APP_DIR/src" python -c 'from npu_hands import is_rk3576; print("yes" if is_rk3576() else "no")')"
if [ "$USE_RK3576_NPU" = "yes" ]; then
  echo "Detected RK3576 (M2): installing RKNN Lite runtime, not conversion toolkit."
  for model in palm_detection_full.rknn hand_landmark_full.rknn; do
    if [ ! -s "$APP_DIR/src/models/$model" ]; then
      echo "ERROR: missing RK3576 model: $APP_DIR/src/models/$model" >&2
      echo "Restore the supplied models before running install.sh again." >&2
      exit 1
    fi
  done
  heartbeat "Installing RK3576 NPU runtime" python -m pip install -r "$APP_DIR/requirements.txt" rknn-toolkit-lite2==2.3.2
  echo "Checking NPU runtime, driver, model loading and inference..."
  PYTHONPATH="$APP_DIR/src" GESTURE_BACKEND=npu python - <<'PY_NPU'
import numpy as np
from npu_hands import create_hands

hands = create_hands()
try:
    blank = np.zeros((360, 480, 3), dtype=np.uint8)
    hands.process(blank)
    # No palm in a blank image: exercise the landmark model explicitly too.
    points, confidence = hands._landmarks(blank, (240, 180, 224, 0))
    if points.shape != (21, 3) or not np.isfinite(points).all() or not np.isfinite(confidence):
        raise RuntimeError("Invalid NPU landmark output")
finally:
    hands.close()
print("RK3576 NPU verification passed.")
PY_NPU
  echo "Default inference backend: RK3576 NPU (auto)."
else
  echo "Non-RK3576 platform (including H1/M1): keeping MediaPipe CPU."
  echo "RKNN packages are not installed; M2 models are not loaded."
fi

echo ""
echo "Deployment complete."
echo "To start the project: cd $APP_DIR/src && python3 main.py"
