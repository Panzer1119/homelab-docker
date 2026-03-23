# AGENTS.md

## Project map (big picture)
- This repo is a catalog of Docker Compose stacks for a homelab; each stack lives under `compose/<category>/<service>/`.
- Categories are purely organizational: `content`, `infrastructure`, `media`, `monitoring`, `tools`, plus shared parts in `compose/common/`.
- Most stacks are self-contained Compose projects with explicit image tags (often pinned with sha256), e.g. `compose/content/immich/docker-compose.yml`.

## Secrets and config flow (1Password)
- Config templates are stored as `ref.*` files (e.g. `compose/content/immich/ref.immich.env`).
- `scripts/inject_secrets.sh` uses `op inject` to turn `ref.*` into real files (e.g. `ref.immich.env` -> `immich.env`, `ref.docker-compose.yml` -> `docker-compose.yml`).
- `op://...` references appear in env files and Compose labels (example: `DB_PASSWORD="op://Docker/Immich/Database/Password"`).

## Volume provisioning pattern
- Compose services declare storage secrets via labels like `de.panzer1119.docker.volume.<name>.<driver>.<key>`.
- Example from `compose/content/archivebox/docker-compose.yml`:
  - `de.panzer1119.docker.volume.archivebox_data.cifs.share=op://Docker/ArchiveBox/CIFS/Share_Data`
- `scripts/process_docker_compose_files.sh` parses all `*docker-compose.yml` files, resolves `op://` values, and calls:
  - `scripts/create_docker_cifs_volume.sh`
  - `scripts/create_docker_rclone_volume.sh`
  - `scripts/create_docker_sshfs_volume.sh`
- Many volumes are marked `external: true`; create them before `docker compose up`.

## Shared Compose parts
- `compose/common/ref.docker-compose.yml` defines shared services like `logging-gelf` and is pulled in via `extends`.
- Stacks commonly `extends: { file: ../../common/docker-compose.yml, service: logging-gelf }` after secrets are injected.

## Critical workflows (manual but expected)
- Generate real env/compose files: `scripts/inject_secrets.sh <dir>` (requires 1Password CLI `op`).
- Create volumes from labels: `scripts/process_docker_compose_files.sh <dir>` (requires `docker`, `jq`, `yq`, `op`).
- Rclone volumes require the Docker rclone plugin and root (`scripts/create_docker_rclone_volume.sh`).

## Conventions to keep
- Keep image versions in Compose (not env) so Renovate can update tags (see comment in `compose/content/immich/ref.immich.env`).
- Use `name:` at top of Compose files (example: `compose/content/archivebox/docker-compose.yml`).
- Prefer storing secrets as `op://` placeholders in `ref.*` files and label values.

