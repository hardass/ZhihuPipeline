#!/bin/bash
# Convenient entry script to launch the Zhihu Obsidian Pipeline

# Get the script directory
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "=== Starting Zhihu to Obsidian Sync Pipeline ==="
PYTHONPATH=src uv run python -m zhihu_pipeline sync "$@"
