## Repository Structure

- [`compose`](./compose/README.md) - Docker Compose projects
    - [`common`](./compose/common/README.md) - Common Docker Compose parts
    - [`content`](./compose/content/README.md) - High-volume data managers that aren't media: archives, docs, photos, web pages
    - [`infrastructure`](./compose/infrastructure/README.md) - Apps that help manage the homelab itself (deployment, sync, orchestration, etc.)
    - [`media`](./compose/media/README.md) - Apps for downloading, enriching, and managing movies, shows, etc.
    - [`monitoring`](./compose/monitoring/README.md) - Tracking performance, logging, usage, health, etc.
    - [`tools`](./compose/tools/README.md) - Utilities supporting other services or general use
- [`scripts`](./scripts/README.md) - Scripts for managing Docker containers, volumes, etc.
