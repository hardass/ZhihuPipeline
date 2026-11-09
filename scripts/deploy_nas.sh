#!/bin/sh
# deploy_nas.sh — ship the committed tree of this repo to the NAS deployment.
#
# The NAS runs the pipeline as the `zhihu-pipeline` docker container whose
# /app/src is a live bind mount of the NAS repo copy, so shipping code means:
# sync the tracked files, then restart the container so the long-running python
# process re-imports them.
#
# Usage:
#   ./scripts/deploy_nas.sh             # sync + verify, restart only if src changed
#   ./scripts/deploy_nas.sh --restart   # sync + verify + always restart
#   ./scripts/deploy_nas.sh --dry-run   # show what would change, touch nothing
#
# Environment overrides: NAS_HOST, NAS_DIR, CONTAINER, QNAP_DOCKER
set -eu

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAS_HOST=${NAS_HOST:-hardass@192.168.1.195}
NAS_DIR=${NAS_DIR:-/share/homes/hardass/zhihu-pipeline}
CONTAINER=${CONTAINER:-zhihu-pipeline}
QNAP_DOCKER=${QNAP_DOCKER:-/share/CACHEDEV3_DATA/.qpkg/container-station/usr/bin/.libs/docker}

# Repo-owned directories: synced with --delete, so files removed from git also
# disappear from the deployment.
TREES="src tests docs"
# Repo-owned top-level files, synced individually. The NAS top level is never
# --delete'd: it also holds runtime-only state (config.yaml, .notify_key,
# logs/, notes/, chrome_profile/, backups/, .venv) that git must not touch.
# .gitignore is deliberately not synced: the deployed copy carries local ignores.
FILES="Dockerfile docker-compose.yml entrypoint.sh pyproject.toml uv.lock sync.sh README.md AGENTS.md config.example.yaml"

DRY_RUN=0
FORCE_RESTART=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --restart) FORCE_RESTART=1 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

log() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

# NAS LAN access has flapped historically; fall back to the MagicDNS name when
# the LAN address refuses SSH (a real ssh handshake, not a port scan).
if ! ssh -o BatchMode=yes -o ConnectTimeout=6 "$NAS_HOST" true 2>/dev/null; then
  FALLBACK=${NAS_FALLBACK:-hardass@nas8c117a}
  if [ "$NAS_HOST" != "$FALLBACK" ] && ssh -o BatchMode=yes -o ConnectTimeout=8 "$FALLBACK" true 2>/dev/null; then
    log "NAS not reachable via $NAS_HOST, falling back to $FALLBACK"
    NAS_HOST=$FALLBACK
  else
    die "cannot reach the NAS via $NAS_HOST (or $FALLBACK)"
  fi
fi

# 0. Never deploy a dirty tree: the deployment must correspond to a real commit.
if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
  git -C "$REPO_ROOT" status --short >&2
  die "working tree is dirty; commit (or stash) before deploying"
fi
COMMIT=$(git -C "$REPO_ROOT" rev-parse --short HEAD)
log "Deploying $COMMIT ($(git -C "$REPO_ROOT" log --oneline -1))"
log "  from $REPO_ROOT"
log "  to   $NAS_HOST:$NAS_DIR"

# 1. Stage exactly the tracked content of HEAD (no .git, no .venv, no pycache).
STAGE=$(mktemp -d "${TMPDIR:-/tmp}/zhihu-deploy.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
git -C "$REPO_ROOT" archive --format=tar HEAD | tar -x -C "$STAGE"

for p in $TREES $FILES; do
  [ -e "$STAGE/$p" ] || die "$p is referenced by deploy_nas.sh but missing from HEAD"
done

# -rlz without -t/-p: content is compared by checksum, NAS-owned permissions and
# timestamps are left alone, so repeated runs report truly zero changes.
RSYNC_BASE="-rlz --checksum"
if [ "$DRY_RUN" -eq 1 ]; then
  RSYNC_ARGS="$RSYNC_BASE --dry-run"
else
  RSYNC_ARGS="$RSYNC_BASE"
fi

CHANGES="$STAGE/.changes"
: > "$CHANGES"

sync_trees() {
  for p in $TREES; do
    # shellcheck disable=SC2086
    rsync $RSYNC_ARGS --delete --itemize-changes --exclude '__pycache__/' \
      "$STAGE/$p/" "$NAS_HOST:$NAS_DIR/$p/"
  done
}

sync_files() {
  for p in $FILES; do
    # shellcheck disable=SC2086
    rsync $RSYNC_ARGS --itemize-changes "$STAGE/$p" "$NAS_HOST:$NAS_DIR/$p"
  done
}

sync_trees >> "$CHANGES"
sync_files >> "$CHANGES"

# Drop directory-only lines (`./`, `.d..`) from the human summary: they carry no
# content and reappear on every run because QNAP dir metadata differs.
CONTENT_CHANGES=$(grep -E '^[<>]' "$CHANGES" || true)
if [ -n "$CONTENT_CHANGES" ]; then
  log "Changes:"
  printf '%s\n' "$CONTENT_CHANGES" | sed 's/^/  /'
else
  log "No changes: deployment already matches $COMMIT."
fi

if [ "$DRY_RUN" -eq 1 ]; then
  log "Dry run: NAS untouched."
  exit 0
fi

# 2. Verify: a second checksum pass must report no pending transfer.
VERIFY="$STAGE/.verify"
: > "$VERIFY"
RSYNC_ARGS="$RSYNC_BASE --dry-run"
sync_trees >> "$VERIFY"
sync_files >> "$VERIFY"
if grep -qE '^[<>]' "$VERIFY"; then
  cat "$VERIFY" >&2
  die "post-deploy verification failed: NAS still differs from $COMMIT"
fi
log "Verified: NAS tracked tree matches $COMMIT."

# 3. Restart only when module code moved, so a docs-only deploy never bounces
#    a running sync pass. --restart forces it.
SRC_CHANGED=0
if printf '%s\n' "$CONTENT_CHANGES" | grep -q 'zhihu_pipeline/'; then
  SRC_CHANGED=1
fi

if [ "$FORCE_RESTART" -eq 1 ] || [ "$SRC_CHANGED" -eq 1 ]; then
  log "Restarting $CONTAINER (src changed or --restart given) ..."
  ssh "$NAS_HOST" "sudo -n $QNAP_DOCKER restart $CONTAINER >/dev/null"
  sleep 8
  STATUS=$(ssh "$NAS_HOST" "sudo -n $QNAP_DOCKER ps --filter name=$CONTAINER --format '{{.Status}}'")
  log "Container: $STATUS"
  case "$STATUS" in
    Up*) : ;;
    *) die "container is not Up after restart" ;;
  esac
else
  log "No python source changes: container left running."
fi

log "Done: $COMMIT deployed."
