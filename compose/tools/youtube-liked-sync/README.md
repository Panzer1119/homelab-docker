[Up](../README.md)

# YouTube Liked Sync

Runs `scripts/sync_liked_videos.py` in a container every 15 minutes (configurable).

## Notes

- Expects `client_secret.json`, `token.json`, and `youtube.db` at repository root.
- `token.json` and `youtube.db` are writable because token refresh and DB updates happen during sync.
- Change interval with `SYNC_INTERVAL_SECONDS` in `docker-compose.yml`.

