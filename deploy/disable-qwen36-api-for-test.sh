#!/usr/bin/env bash
set -euo pipefail

# Gate the Qwen3.6 public route before stopping its vLLM process.
# The Nginx server authenticates requests before entering this location, so a
# no-auth probe may be 401 even when the maintenance gate is correctly active.
STATE_DIR="/var/lib/peril-gb10-qwen3-coder-next"
SNIPPET="/etc/nginx/snippets/qwen36.conf"
MAINTENANCE_SOURCE="/home/perilops/qwen3-coder-next-preload/qwen36-maintenance.conf"
ORIGINAL="$STATE_DIR/qwen36.conf.original"
PID_FILE="/home/perilops/vllm-qwen36/run/vllm.pid"

sudo -n true
test -r "$MAINTENANCE_SOURCE"
sudo install -d -m 0755 "$STATE_DIR"
if ! sudo test -e "$ORIGINAL"; then
  sudo cp -a "$SNIPPET" "$ORIGINAL"
fi

sudo install -m 0644 "$MAINTENANCE_SOURCE" "$SNIPPET"
sudo nginx -t
sudo systemctl reload nginx

if ! sudo grep -q "Qwen3.6 API disabled for controlled GB10 model test" "$SNIPPET"; then
  echo "Qwen3.6 maintenance gate was not installed; refusing to stop vLLM." >&2
  exit 1
fi

code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:11435/qwen36/health)"
if [[ "$code" != 503 && "$code" != 401 ]]; then
  echo "Unexpected Qwen3.6 gate response (got $code); refusing to stop vLLM." >&2
  exit 1
fi

if [[ -r "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE")"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    echo "Stopping Qwen3.6 vLLM PID $pid..."
    kill -TERM "$pid"
    for _ in {1..60}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 2
    done
  fi
fi

if ss -ltn | grep -qE ':8000[[:space:]]'; then
  echo 'Port 8000 is still listening; refusing to continue to model loading.' >&2
  exit 1
fi

echo 'Qwen3.6 public API is gated and port 8000 is released.'
echo 'Do not load another model until free memory/GPU state has been rechecked.'
