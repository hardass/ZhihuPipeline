#!/bin/bash
set -e

# Clean up any stale X locks from previous container runs
rm -f /tmp/.X*-lock /tmp/.X11-unix/X* 2>/dev/null || true

# Start Xvfb virtual framebuffer on display :99
export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

# Brief pause for Xvfb initialization and verify
sleep 1
if ! kill -0 $XVFB_PID 2>/dev/null; then
    echo "[ERROR] Xvfb failed to start on display :99!"
    exit 1
fi
echo "[INFO] Xvfb virtual screen :99 successfully started (PID: $XVFB_PID)"

# Execute requested zhihu-pipeline command
if [ "$#" -eq 0 ]; then
    exec python -m zhihu_pipeline sync
else
    exec python -m zhihu_pipeline "$@"
fi
