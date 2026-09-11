#!/usr/bin/env bash
# FISHRAND run script — automates steps 2+ (vision tracker, crypto server,
# optional web apps, optional CLI smoke test). Step 1 (connecting droidcam /
# v4l2loopback and finding its device index) is still manual — see README.
#
# Usage:
#   ./run.sh                       vision.py + crypto server, foreground
#   ./run.sh --web                 ...plus dashboard (5173) + diary (5174)
#   ./run.sh --cli-test            one-shot encrypt/decrypt smoke test, then stop
#   ./run.sh --camera-index 2      point vision.py at /dev/video2 (droidcam)
#   ./run.sh --inbox /path         dir vision.py's fish_log.json lives in
#   ./run.sh --stop                stop everything a previous run started
#   ./run.sh --skip-vision         don't start vision.py (e.g. already running)
#
# Press Ctrl+C to stop every service this script started.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PIDDIR="$ROOT/.run"
LOGDIR="$ROOT/logs"
mkdir -p "$PIDDIR" "$LOGDIR"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"

WITH_WEB=0
CLI_TEST=0
DO_STOP=0
SKIP_VISION=0
CAMERA_INDEX=""
INBOX_DIR="$ROOT"

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --web) WITH_WEB=1; shift ;;
    --cli-test) CLI_TEST=1; shift ;;
    --stop) DO_STOP=1; shift ;;
    --skip-vision) SKIP_VISION=1; shift ;;
    --camera-index) CAMERA_INDEX="$2"; shift 2 ;;
    --inbox) INBOX_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

pidfile() { echo "$PIDDIR/$1.pid"; }

is_running() {
  local f; f="$(pidfile "$1")"
  [[ -f "$f" ]] && kill -0 "$(cat "$f")" 2>/dev/null
}

stop_all() {
  echo
  echo "[stop] shutting down FISHRAND services..."
  local f pid name
  for f in "$PIDDIR"/*.pid; do
    [[ -e "$f" ]] || continue
    pid="$(cat "$f")"
    name="$(basename "$f" .pid)"
    if kill -0 "$pid" 2>/dev/null; then
      echo "  - $name (pid $pid)"
      kill "$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "$f"
  done
}

if [[ $DO_STOP -eq 1 ]]; then
  stop_all
  exit 0
fi

start_bg() {
  # start_bg <name> <cmd...>  -> logs to logs/<name>.log, tracks pid
  local name="$1"; shift
  echo "[start] $name  (log: logs/$name.log)"
  nohup "$@" >"$LOGDIR/$name.log" 2>&1 &
  echo $! > "$(pidfile "$name")"
}

# --- env setup -----------------------------------------------------------
if [[ ! -x "$PY" ]]; then
  echo "[setup] creating venv..."
  python3 -m venv "$VENV"
fi
# Cheap even when already installed; catches a fresh clone or a venv that's
# missing opencv (vision.py needs it, but it's not in requirements.txt
# since the crypto side runs fine without a camera).
"$PIP" install -q -r requirements.txt -r requirements-vision.txt
if ! command -v npm >/dev/null 2>&1 && [[ $WITH_WEB -eq 1 ]]; then
  echo "[error] --web needs Node/npm, which isn't installed. On Arch:"
  echo "        sudo pacman -S nodejs npm"
  echo "        (dashboard/diary will be skipped this run)"
  WITH_WEB=0
fi

trap stop_all EXIT INT TERM

# --- step 2: vision tracker --------------------------------------------
if [[ $SKIP_VISION -eq 1 ]]; then
  echo "[skip] vision.py (--skip-vision)"
elif is_running vision; then
  echo "[skip] vision.py already running (pid $(cat "$(pidfile vision)"))"
else
  [[ -n "$CAMERA_INDEX" ]] && export FISHRAND_CAMERA_INDEX="$CAMERA_INDEX"
  echo "[info] vision.py opens a live preview window — needs a display (X11/Wayland)."
  start_bg vision "$PY" vision.py
  sleep 2
  if ! is_running vision; then
    echo "[warn] vision.py exited immediately — check logs/vision.log (often: wrong camera index, or no display)."
  fi
fi

# --- step 3: crypto server ---------------------------------------------
export FISHRAND_FISH_INBOX=1
export FISHRAND_FISH_INBOX_DIR="$INBOX_DIR"
start_bg server "$VENV/bin/uvicorn" server.main:app --port 8000

echo "[wait] waiting for server on :8000 ..."
ok=0
for _ in $(seq 1 30); do
  if curl -sf localhost:8000/api/health >/dev/null 2>&1; then ok=1; break; fi
  sleep 0.5
done
if [[ $ok -eq 1 ]]; then
  curl -s localhost:8000/api/health | "$PY" -m json.tool
else
  echo "[warn] server did not respond in time — check logs/server.log"
fi

# --- optional: web apps --------------------------------------------------
if [[ $WITH_WEB -eq 1 ]]; then
  [[ -d dashboard/node_modules ]] || (cd dashboard && npm install)
  [[ -d diary/node_modules ]] || (cd diary && npm install)
  start_bg dashboard bash -c "cd '$ROOT/dashboard' && npm run dev -- --port 5173"
  start_bg diary bash -c "cd '$ROOT/diary' && npm run dev -- --port 5174"
  echo "[web] dashboard -> http://localhost:5173"
  echo "[web] diary     -> http://localhost:5174"
fi

# --- optional: one-shot CLI smoke test -----------------------------------
if [[ $CLI_TEST -eq 1 ]]; then
  USBDIR="$ROOT/.demo_usb"
  mkdir -p "$USBDIR"
  [[ -f "$USBDIR/code.txt" ]] || "$PY" cli.py init --dir "$USBDIR"
  echo "[cli-test] encrypting examples/diary.txt with the live/inbox fish window..."
  "$PY" cli.py encrypt --diary examples/diary.txt --usb "$USBDIR" --out "$USBDIR/diary.pkg"
  echo "[cli-test] decrypting it back..."
  "$PY" cli.py decrypt --pkg "$USBDIR/diary.pkg" --usb "$USBDIR"
  exit 0   # trap stops vision/server/web on the way out
fi

echo
echo "[ready] FISHRAND is running. Logs in ./logs/. Press Ctrl+C to stop everything."
wait
