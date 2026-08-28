#!/bin/bash
set -e

# Start Xvfb virtual framebuffer on display :99
export DISPLAY=:99
Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset > /dev/null 2>&1 &
XVFB_PID=$!

# Ensure Xvfb is terminated when the container exits
cleanup() {
    kill -9 $XVFB_PID 2>/dev/null || true
}
trap cleanup EXIT

# Brief pause for Xvfb initialization
sleep 1

# Execute requested zhihu-pipeline command
if [ "$#" -eq 0 ]; then
    exec python -m zhihu_pipeline sync
else
    exec python -m zhihu_pipeline "$@"
fi
