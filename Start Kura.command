#!/bin/bash
# ---------------------------------------------------------------------------
#  Kura launcher for macOS (also works on Linux)
#
#  First run: installs "uv" (Python manager) if needed, creates a private
#  Python environment in ~/Library/Application Support/Kura and installs the
#  libraries.  Later runs start immediately.
#  Nothing is written into this (Google Drive) folder.
# ---------------------------------------------------------------------------
set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$APP_DIR" || exit 1

if [ "$(uname)" = "Darwin" ]; then
  KURA_HOME="${KURA_HOME:-$HOME/Library/Application Support/Kura}"
else
  KURA_HOME="${KURA_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/kura}"
fi
VENV="$KURA_HOME/venv"
PY="$VENV/bin/python"
REQ="$APP_DIR/requirements.txt"
STAMP="$VENV/.kura-requirements.txt"
export KURA_HOME
export PYTHONPYCACHEPREFIX="$KURA_HOME/pycache"   # keep __pycache__ out of the app folder
mkdir -p "$KURA_HOME/logs"

pause_exit() { echo; read -r -p "Press Enter to close… / Enter で閉じます" _; exit 1; }

echo "=============================================="
echo "  Kura (蔵) – starting / 起動しています"
echo "=============================================="

# --- 1. uv -------------------------------------------------------------------
find_uv() {
  for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" \
           "/opt/homebrew/bin/uv" "/usr/local/bin/uv"; do
    if [ -n "$c" ] && [ -x "$c" ]; then echo "$c"; return 0; fi
  done
  return 1
}
UV="$(find_uv)"
if [ -z "$UV" ]; then
  echo "[1/3] Installing uv (https://astral.sh/uv) … / uv をインストールしています"
  if ! command -v curl >/dev/null 2>&1; then echo "curl not found."; pause_exit; fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV="$(find_uv)"
  if [ -z "$UV" ]; then echo "ERROR: uv could not be installed."; pause_exit; fi
else
  echo "[1/3] uv: $UV"
fi
export KURA_UV="$UV"

# --- 2. Python environment ------------------------------------------------------
if [ ! -x "$PY" ]; then
  echo "[2/3] Creating the Python environment (first run only) / Python 環境を作成しています（初回のみ）"
  "$UV" venv --python "${KURA_PYTHON:-3.12}" --python-preference "${KURA_PY_PREF:-only-managed}" --seed "$VENV" || { echo "ERROR: could not create the environment."; pause_exit; }
  rm -f "$STAMP"
else
  echo "[2/3] Python environment: OK"
fi

# --- 3. Libraries --------------------------------------------------------------------
if ! cmp -s "$REQ" "$STAMP"; then
  echo "[3/3] Installing libraries (takes a minute the first time) / ライブラリをインストールしています"
  if "$UV" pip install --python "$PY" -r "$REQ"; then
    cp "$REQ" "$STAMP"
  else
    echo "ERROR: library installation failed (internet connection?)."
    pause_exit
  fi
else
  echo "[3/3] Libraries: OK"
fi

# --- Launch --------------------------------------------------------------------------
if [ "${1:-}" = "debug" ]; then
  exec "$PY" -m kura
fi
echo "Launching Kura … / Kura を起動します"
nohup "$PY" -m kura >> "$KURA_HOME/logs/launcher.log" 2>&1 &
disown 2>/dev/null || true
sleep 1
echo "Kura is running. You can close this window. / このウィンドウは閉じて構いません。"
exit 0
