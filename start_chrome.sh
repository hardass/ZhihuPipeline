#!/bin/bash
# Start Chrome in debugging mode on macOS

echo "Starting Google Chrome with remote debugging port 9222..."
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.zhihu_pipeline/chrome_profile" \
  --no-first-run \
  --no-default-browser-check
