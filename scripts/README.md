[Up](../README.md)

# Scripts

## `snapshot_docker_compose_stack.py`

Python replacement for `snapshot_docker_compose_stack.sh` with built-in git worktree flow (like the old command template in `generate_changes_html.py`).

### Highlights

- Uses a temporary git worktree at `--commit` (default: `HEAD`) and copies injected/non-`ref.*` env files.
- Infers section/stack from `--directory` (same behavior as the shell script).
- If `--target-container` is omitted, picks the first service in compose config for image metadata.
- Snapshots bind-mounted ZFS datasets under default base prefixes.
- If `--up-after` is set and compose files use `PWD` paths, it skips `up` in worktree and prints a manual `cd ... && docker compose up -d` command.

### Example

```bash
sudo python3 scripts/snapshot_docker_compose_stack.py \
  -d compose/content/immich \
  -c immich_server \
  -u \
  -C HEAD \
  --repo "$(pwd)" \
  -v
```

### Dry run

```bash
python3 scripts/snapshot_docker_compose_stack.py -d compose/content/immich -u -N -v
```

