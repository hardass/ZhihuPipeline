#!/bin/sh
# Preflight for the Zhihu LiveSync node.
#
# Why this exists: the Zhihu vault directory was once assembled by hand while the
# pipeline was still writing attachments to assets/<note title>/. One note therefore
# referenced an attachment directory that the copy step never brought along; the node
# published that broken note over the good remote version and every other device got a
# conflict dialog. A node must refuse to run against such a snapshot instead of
# overwriting healthy notes.
#
# Exit codes: 0 = snapshot is consistent, safe to mirror/sync
#             1 = stale or missing attachment references, do NOT publish
#
# POSIX sh + grep/find only: the node image has no Python, and the NAS shell is BusyBox.

set -eu

VAULT="${VAULT_PATH:-/vault}"
COLLECTION="${COLLECTION_DIR:-知乎收藏}"
ALLOWED='知乎附件|知乎视频'          # attachment prefixes owned by this pipeline

fail() {
  echo "[preflight] FAIL: $1" >&2
  exit 1
}

[ -d "$VAULT/$COLLECTION" ] || fail "找不到笔记目录 $VAULT/$COLLECTION（快照不完整？）"

# 1) No note may reference attachments outside the dedicated prefixes.
STALE=$(grep -rhoE '\.\./\.\./assets/[^/)]+/' "$VAULT/$COLLECTION" 2>/dev/null \
  | sed -E 's#\.\./\.\./assets/([^/]+)/#\1#' \
  | grep -vE "^($ALLOWED)$" | sort -u || true)
if [ -n "$STALE" ]; then
  echo "$STALE" | head -10 >&2
  fail "仍有笔记引用非专用前缀的附件目录（上面列出前 10 个），先跑 migrate-attachments --apply"
fi

# 2) Every referenced attachment file must exist; a partially copied snapshot is worse
#    than none because publishing it would break images on every device.
TMP_MISSING=$(mktemp)
trap 'rm -f "$TMP_MISSING"' EXIT
find "$VAULT/$COLLECTION" -name '*.md' 2>/dev/null | while read -r note; do
  dir=$(dirname "$note")
  grep -oE '\.\./\.\./assets/[^)]+' "$note" 2>/dev/null | while read -r ref; do
    target="$dir/$ref"
    if [ ! -e "$target" ]; then
      plain=$(printf '%s' "$target" | sed 's/%20/ /g')
      [ -e "$plain" ] || echo "$target" >> "$TMP_MISSING"
    fi
  done
done
MISSING_COUNT=$(wc -l < "$TMP_MISSING" | tr -d ' ')
if [ "$MISSING_COUNT" -gt 0 ]; then
  head -10 "$TMP_MISSING" >&2
  fail "有 $MISSING_COUNT 处附件引用指向不存在的文件，快照不完整，禁止发布"
fi

echo "[preflight] OK: 附件引用全部落在专用前缀下，且文件齐备"
