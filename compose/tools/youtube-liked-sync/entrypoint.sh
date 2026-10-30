#!/bin/sh
set -eu

INTERVAL="${SYNC_INTERVAL_SECONDS:-900}"

echo "Starting YouTube liked video sync loop (interval: ${INTERVAL}s)"

while true; do
  echo "[$(date -Iseconds)] Running sync_liked_videos.py"
  if ! python -u /app/sync_liked_videos.py; then
    echo "[$(date -Iseconds)] Sync failed; retrying after ${INTERVAL}s"
  else
    echo "[$(date -Iseconds)] Sync finished"
  fi

  sleep "${INTERVAL}"
done
