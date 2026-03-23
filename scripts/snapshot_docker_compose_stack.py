#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


KEY_SECTION_NAME = "de.panzer1119.docker:section_name"
KEY_STACK_NAME = "de.panzer1119.docker:stack_name"
KEY_TARGET_IMAGE = "de.panzer1119.docker:target_image"
KEY_TARGET_TAG = "de.panzer1119.docker:target_tag"
KEY_TARGET_SHA256 = "de.panzer1119.docker:target_sha256"
KEY_GIT_COMMIT_SHA1 = "de.panzer1119.docker:git_commit_sha1"

DEFAULT_SNAPSHOT_PREFIX = "stack-checkpoint"
DEFAULT_HOLD_NAME = "stack-checkpoint"
DEFAULT_BASE_DATASETS = [
    "docker/config",
    "docker/data",
    "docker/volumes/config",
    "docker/volumes/data",
]

INJECTED_FILES_MARKER = "# Ignore injected ref files"
PWD_PATTERN = re.compile(r"(\$\{PWD(?:[:?][^}]*)?}|\$PWD\b)")


class CliError(RuntimeError):
    pass


@dataclass(slots=True)
class StackLocation:
    compose_root: Path
    section: str
    stack: str
    stack_dir: Path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Snapshot bind-mounted ZFS datasets of a Docker Compose stack. "
            "By default, runs in a temporary git worktree at the selected commit."
        )
    )
    parser.add_argument("-d", "--directory", default=".", help="Working directory (default: current directory)")
    parser.add_argument("-S", "--section", help="Compose section name (e.g. content, monitoring)")
    parser.add_argument("-n", "--name", "--stack", dest="stack", help="Compose stack/project name")

    parser.add_argument("-i", "--target-image", help="Image path for snapshot metadata")
    parser.add_argument("-t", "--target-tag", help="Image tag for snapshot metadata")
    parser.add_argument("-s", "--target-sha256", help="Image sha256 (without prefix) for snapshot metadata")
    parser.add_argument(
        "-c",
        "--target-container",
        help="Service/container to derive image metadata from (default: first compose service)",
    )

    parser.add_argument("-C", "--commit-sha1", "--commit", dest="commit", default="HEAD", help="Git commit to use")
    parser.add_argument("--repo", help="Git repository root (default: auto-detected from --directory)")
    parser.add_argument("--no-worktree", action="store_true", help="Run directly in repository instead of temp worktree")
    parser.add_argument("--keep-worktree", action="store_true", help="Keep temporary worktree for inspection")

    parser.add_argument("-p", "--snapshot-prefix", default=DEFAULT_SNAPSHOT_PREFIX, help="Snapshot name prefix")
    parser.add_argument(
        "--hold-name",
        default=DEFAULT_HOLD_NAME,
        help="ZFS hold tag used for created snapshots (default: %(default)s)",
    )
    parser.add_argument(
        "--no-hold-snapshots",
        dest="hold_snapshots",
        action="store_false",
        help="Do not place a ZFS hold on created snapshots",
    )
    parser.set_defaults(hold_snapshots=True)
    parser.add_argument(
        "-b",
        "--base-dataset",
        action="append",
        dest="base_datasets",
        help="Allowed dataset prefix (repeatable, default is built-in set)",
    )

    parser.add_argument("-u", "--up-after", action="store_true", help="Start stack again after snapshot")
    parser.add_argument("-N", "--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("-D", "--debug", action="store_true", help="Enable debug logging")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="Only print errors")
    return parser.parse_args(argv)


def configure_logging(args: argparse.Namespace) -> None:
    if args.quiet:
        level = logging.ERROR
    elif args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command_text = " ".join(shlex_quote(p) for p in command)
    if dry_run:
        logging.info("[DRY RUN] %s", command_text)
        return subprocess.CompletedProcess(command, 0, "", "")
    logging.debug("Running: %s", command_text)
    return subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=capture_output,
        check=True,
    )


def shlex_quote(value: str) -> str:
    if value == "":
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value):
        return value
    return "'" + value.replace("'", "'\\''") + "'"


def command_output(command: list[str], *, cwd: Path | None = None) -> str:
    result = subprocess.run(command, cwd=str(cwd) if cwd else None, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def detect_repo_root(directory: Path, explicit_repo: str | None) -> Path:
    if explicit_repo:
        repo = Path(explicit_repo).expanduser().resolve()
        if not repo.is_dir():
            raise CliError(f"Repository does not exist: {repo}")
        return repo
    try:
        output = command_output(["git", "-C", str(directory), "rev-parse", "--show-toplevel"])
    except subprocess.CalledProcessError as exc:
        raise CliError("Could not detect git repository root. Use --repo.") from exc
    return Path(output).resolve()


def resolve_stack_location(args: argparse.Namespace) -> StackLocation:
    directory = Path(args.directory).expanduser().resolve()
    if not directory.is_dir():
        raise CliError(f"Directory does not exist: {directory}")

    compose_root = directory
    stack = args.stack
    section = args.section

    if not stack:
        stack = compose_root.name
        compose_root = compose_root.parent
        logging.debug("Inferred stack from directory: %s", stack)

    if not section:
        section = compose_root.name
        compose_root = compose_root.parent
        logging.debug("Inferred section from directory: %s", section)

    stack_dir = compose_root / section / stack
    if not stack_dir.is_dir():
        raise CliError(f"Stack directory not found: {stack_dir}")

    return StackLocation(compose_root=compose_root, section=section, stack=stack, stack_dir=stack_dir)


def compose_files(stack_dir: Path) -> tuple[Path, Path | None]:
    primary = None
    for candidate in ("docker-compose.yml", "docker-compose.yaml"):
        path = stack_dir / candidate
        if path.is_file():
            primary = path
            break
    if primary is None:
        raise CliError(f"No docker-compose file found in {stack_dir}")

    override = None
    for candidate in ("docker-compose.override.yml", "docker-compose.override.yaml"):
        path = stack_dir / candidate
        if path.is_file():
            override = path
            break
    return primary, override


def docker_compose_json(primary: Path, override: Path | None, *, dry_run: bool) -> dict:
    command = ["docker", "compose", "-f", str(primary)]
    if override:
        command.extend(["-f", str(override)])
    command.extend(["config", "--format", "json"])
    if dry_run:
        logging.info("[DRY RUN] Would resolve compose config for %s", primary.parent)
        return {}
    result = run_command(command, capture_output=True)
    return json.loads(result.stdout)


def compose_cmd(primary: Path, override: Path | None) -> list[str]:
    command = ["docker", "compose", "-f", str(primary)]
    if override:
        command.extend(["-f", str(override)])
    return command


def choose_service(compose_config: dict, target_container: str | None) -> str | None:
    services = compose_config.get("services", {})
    if not isinstance(services, dict) or not services:
        return None

    if target_container:
        if target_container in services:
            return target_container
        for service_name, service in services.items():
            if isinstance(service, dict) and service.get("container_name") == target_container:
                return service_name
        raise CliError(f"Service/container not found in compose config: {target_container}")

    return next(iter(services.keys()))


def parse_image_reference(image_ref: str) -> tuple[str, str, str]:
    raw = image_ref.strip()
    digest = ""
    if "@sha256:" in raw:
        raw, digest = raw.split("@sha256:", 1)

    tag = "latest"
    last_slash = raw.rfind("/")
    last_colon = raw.rfind(":")
    if last_colon > last_slash:
        raw, tag = raw[:last_colon], raw[last_colon + 1 :]

    repository = "docker.io"
    user = "_"
    image_name = raw
    if "/" in raw:
        first, rest = raw.split("/", 1)
        if "." in first or ":" in first or first == "localhost":
            repository = first
            if "/" in rest:
                user, image_name = rest.split("/", 1)
            else:
                image_name = rest
        else:
            user = first
            image_name = rest

    image_path = f"{repository}/{user}/{image_name}"
    return image_path, tag, digest


def derive_target_metadata(
    compose_config: dict,
    target_container: str | None,
    target_image: str | None,
    target_tag: str | None,
    target_sha256: str | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    service_name = choose_service(compose_config, target_container)
    if service_name is None:
        return target_image, target_tag, target_sha256, None

    if target_image and target_tag and target_sha256 is not None:
        return target_image, target_tag, target_sha256, service_name

    service = compose_config.get("services", {}).get(service_name, {})
    image_ref = service.get("image") if isinstance(service, dict) else None
    if not image_ref:
        return target_image, target_tag, target_sha256, service_name

    parsed_image, parsed_tag, parsed_sha = parse_image_reference(image_ref)
    return (
        target_image or parsed_image,
        target_tag or parsed_tag,
        target_sha256 if target_sha256 is not None else parsed_sha,
        service_name,
    )


def derive_metadata_from_running_stack(
    *,
    primary: Path,
    override: Path | None,
    target_container: str | None,
    dry_run: bool,
) -> tuple[str, str, str] | None:
    if dry_run:
        return None

    config = docker_compose_json(primary, override, dry_run=False)
    service_name = choose_service(config, target_container)
    if service_name is None:
        return None

    command = compose_cmd(primary, override)
    result = run_command(command + ["ps", "-q", service_name], capture_output=True)
    container_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not container_ids:
        return None

    inspect = run_command(["docker", "inspect", "--format", "{{.Config.Image}}", container_ids[0]], capture_output=True)
    image_ref = inspect.stdout.strip()
    if not image_ref:
        return None
    return parse_image_reference(image_ref)


def read_file_from_commit(repo_root: Path, commit_ref: str, rel_path: Path) -> str | None:
    git_path = rel_path.as_posix()
    exists = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", f"{commit_ref}:{git_path}"],
        text=True,
        capture_output=True,
    )
    if exists.returncode != 0:
        return None
    result = run_command(["git", "-C", str(repo_root), "show", f"{commit_ref}:{git_path}"], capture_output=True)
    return result.stdout


def derive_metadata_from_previous_commit(
    *,
    repo_root: Path,
    stack_rel: Path,
    target_container: str | None,
    commit_sha1: str,
    dry_run: bool,
) -> tuple[str, str, str] | None:
    if dry_run:
        return None

    previous_ref = f"{commit_sha1}^"
    with tempfile.TemporaryDirectory(prefix="homelab-docker-prev-commit-") as tmp:
        tmp_dir = Path(tmp)
        primary: Path | None = None
        override: Path | None = None

        for candidate in ("docker-compose.yml", "docker-compose.yaml"):
            rel = stack_rel / candidate
            content = read_file_from_commit(repo_root, previous_ref, rel)
            if content is not None:
                primary = tmp_dir / candidate
                primary.write_text(content, encoding="utf-8")
                break

        if primary is None:
            return None

        for candidate in ("docker-compose.override.yml", "docker-compose.override.yaml"):
            rel = stack_rel / candidate
            content = read_file_from_commit(repo_root, previous_ref, rel)
            if content is not None:
                override = tmp_dir / candidate
                override.write_text(content, encoding="utf-8")
                break

        config = docker_compose_json(primary, override, dry_run=False)
        service_name = choose_service(config, target_container)
        if service_name is None:
            return None

        service = config.get("services", {}).get(service_name, {})
        image_ref = service.get("image") if isinstance(service, dict) else None
        if not image_ref:
            return None
        return parse_image_reference(image_ref)


def extract_bind_datasets(compose_config: dict) -> list[str]:
    sources: set[str] = set()

    top_volumes = compose_config.get("volumes", {})
    if isinstance(top_volumes, dict):
        for definition in top_volumes.values():
            if not isinstance(definition, dict):
                continue
            driver = definition.get("driver")
            opts = definition.get("driver_opts")
            if driver == "local" and isinstance(opts, dict) and "bind" in str(opts.get("o", "")):
                source = str(opts.get("device", ""))
                if source.startswith("/"):
                    sources.add(source)

    services = compose_config.get("services", {})
    if isinstance(services, dict):
        for service in services.values():
            if not isinstance(service, dict):
                continue
            for volume in service.get("volumes", []):
                if not isinstance(volume, dict):
                    continue
                if volume.get("type") == "bind":
                    source = str(volume.get("source", ""))
                    if source.startswith("/"):
                        sources.add(source)

    return sorted(source.lstrip("/") for source in sources)


def dataset_allowed(dataset: str, allowed_prefixes: list[str]) -> bool:
    for prefix in allowed_prefixes:
        p = prefix.rstrip("/")
        if dataset == p or dataset.startswith(f"{p}/"):
            return True
    return False


def generate_snapshot_name(prefix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{timestamp}"


def set_snapshot_property(snapshot: str, key: str, value: str, *, dry_run: bool) -> None:
    if not value:
        return
    run_command(["zfs", "set", f"{key}={value}", snapshot], dry_run=dry_run)


def snapshot_dataset(
    dataset: str,
    snapshot_name: str,
    *,
    hold_snapshots: bool,
    hold_name: str,
    section: str,
    stack: str,
    target_image: str | None,
    target_tag: str | None,
    target_sha256: str | None,
    commit_sha1: str,
    dry_run: bool,
) -> None:
    snapshot = f"{dataset}@{snapshot_name}"
    run_command(["zfs", "snapshot", snapshot], dry_run=dry_run)
    if hold_snapshots:
        run_command(["zfs", "hold", hold_name, snapshot], dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_SECTION_NAME, section, dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_STACK_NAME, stack, dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_TARGET_IMAGE, target_image or "", dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_TARGET_TAG, target_tag or "", dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_TARGET_SHA256, target_sha256 or "", dry_run=dry_run)
    set_snapshot_property(snapshot, KEY_GIT_COMMIT_SHA1, commit_sha1, dry_run=dry_run)


def copy_injected_files(repo: Path, worktree: Path) -> None:
    gitignore = repo / ".gitignore"
    if not gitignore.is_file():
        return

    in_block = False
    for line in gitignore.read_text(encoding="utf-8").splitlines():
        if line.strip() == INJECTED_FILES_MARKER:
            in_block = True
            continue
        if not in_block:
            continue
        path_str = line.strip()
        if not path_str:
            break
        if path_str.startswith("#"):
            continue
        rel = Path(path_str.lstrip("/"))
        source = repo / rel
        target = worktree / rel
        if not source.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_non_ref_env_files(repo: Path, worktree: Path) -> None:
    for env_file in repo.rglob("*.env"):
        if env_file.name.startswith("ref."):
            continue
        if ".git" in env_file.parts:
            continue
        rel = env_file.relative_to(repo)
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(env_file, target)


@contextmanager
def maybe_worktree(repo: Path, commit: str, use_worktree: bool, keep_worktree: bool):
    if not use_worktree:
        yield repo
        return

    worktree_path = Path(tempfile.mkdtemp(prefix="homelab-docker-wt-"))
    try:
        run_command(["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree_path), commit])
        copy_injected_files(repo, worktree_path)
        copy_non_ref_env_files(repo, worktree_path)
        yield worktree_path
    finally:
        if keep_worktree:
            logging.warning("Keeping temporary worktree at: %s", worktree_path)
        else:
            try:
                run_command(["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_path)])
            except Exception as exc:  # pragma: no cover
                logging.error("Failed to remove worktree: %s", exc)
            shutil.rmtree(worktree_path, ignore_errors=True)


def compose_command_for_manual_up(primary: Path, override: Path | None, stack_dir: Path) -> str:
    parts = [f"cd {shlex_quote(str(stack_dir))}", "&&", "docker", "compose", "-f", shlex_quote(primary.name)]
    if override:
        parts.extend(["-f", shlex_quote(override.name)])
    parts.extend(["up", "-d"])
    return " ".join(parts)


def should_skip_up_in_worktree(primary: Path, override: Path | None) -> bool:
    files = [primary] + ([override] if override else [])
    for file_path in files:
        content = file_path.read_text(encoding="utf-8")
        if PWD_PATTERN.search(content):
            logging.warning("Detected PWD-based path in %s; skipping automatic 'up' in worktree.", file_path)
            return True
    return False


def ensure_requirements(*, dry_run: bool, use_worktree: bool) -> None:
    commands: tuple[str, ...] = ("docker", "git", "zfs")
    for cmd_name in commands:
        if shutil.which(str(cmd_name)) is None:
            raise CliError(f"Missing required command: {cmd_name}")

    if not dry_run and os.geteuid() != 0:
        raise CliError("This script must run as root (or via sudo) unless --dry-run is used.")

    if dry_run:
        return

    run_command(["docker", "ps"], capture_output=True)
    run_command(["zfs", "list"], capture_output=True)
    if use_worktree:
        run_command(["git", "worktree", "list"], capture_output=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    configure_logging(args)

    try:
        location = resolve_stack_location(args)
        repo_root = detect_repo_root(location.stack_dir, args.repo)
        use_worktree = not args.no_worktree
        ensure_requirements(dry_run=args.dry_run, use_worktree=use_worktree)

        commit_sha1 = command_output(["git", "-C", str(repo_root), "rev-parse", args.commit])
        logging.info("Using commit: %s", commit_sha1)

        stack_rel = location.stack_dir.relative_to(repo_root)
        base_datasets = args.base_datasets or DEFAULT_BASE_DATASETS
        hold_name = args.hold_name.strip()
        if args.hold_snapshots and not hold_name:
            raise CliError("--hold-name must not be empty when snapshot holds are enabled.")

        real_primary, real_override = compose_files(location.stack_dir)
        running_metadata = derive_metadata_from_running_stack(
            primary=real_primary,
            override=real_override,
            target_container=args.target_container,
            dry_run=args.dry_run,
        )
        previous_commit_metadata = None
        if running_metadata is None:
            previous_commit_metadata = derive_metadata_from_previous_commit(
                repo_root=repo_root,
                stack_rel=stack_rel,
                target_container=args.target_container,
                commit_sha1=commit_sha1,
                dry_run=args.dry_run,
            )

        with maybe_worktree(repo_root, commit_sha1, use_worktree=use_worktree, keep_worktree=args.keep_worktree) as base:
            effective_stack_dir = (base / stack_rel) if use_worktree else location.stack_dir
            primary, override = compose_files(effective_stack_dir)
            compose_config = docker_compose_json(primary, override, dry_run=args.dry_run)

            target_image, target_tag, target_sha256, service_name = derive_target_metadata(
                compose_config,
                args.target_container,
                args.target_image,
                args.target_tag,
                args.target_sha256,
            )
            if service_name:
                logging.info("Using service for metadata: %s", service_name)

            if running_metadata:
                source_image, source_tag, source_sha = running_metadata
                logging.info("Using image metadata from running stack")
                target_image = args.target_image or source_image
                target_tag = args.target_tag or source_tag
                target_sha256 = args.target_sha256 if args.target_sha256 is not None else source_sha
            elif previous_commit_metadata:
                source_image, source_tag, source_sha = previous_commit_metadata
                logging.info("Stack not running; using image metadata from previous commit compose")
                target_image = args.target_image or source_image
                target_tag = args.target_tag or source_tag
                target_sha256 = args.target_sha256 if args.target_sha256 is not None else source_sha

            datasets = extract_bind_datasets(compose_config)
            allowed_datasets = [d for d in datasets if dataset_allowed(d, base_datasets)]
            snapshot_name = generate_snapshot_name(args.snapshot_prefix)

            compose_cmdline = compose_cmd(primary, override)

            run_command(compose_cmdline + ["down"], dry_run=args.dry_run)

            for dataset in allowed_datasets:
                snapshot_dataset(
                    dataset,
                    snapshot_name,
                    hold_snapshots=args.hold_snapshots,
                    hold_name=hold_name,
                    section=location.section,
                    stack=location.stack,
                    target_image=target_image,
                    target_tag=target_tag,
                    target_sha256=target_sha256,
                    commit_sha1=commit_sha1,
                    dry_run=args.dry_run,
                )

            if args.up_after:
                skip_up = use_worktree and should_skip_up_in_worktree(primary, override)
                if skip_up:
                    manual_cmd = compose_command_for_manual_up(
                        location.stack_dir / primary.name,
                        location.stack_dir / override.name if override else None,
                        location.stack_dir,
                    )
                    logging.warning("Manual recovery command: %s", manual_cmd)
                else:
                    run_command(compose_cmdline + ["up", "-d"], dry_run=args.dry_run)

        logging.info("Done.")
        return 0

    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else str(exc)
        logging.error("Command failed: %s", stderr)
        return 1
    except CliError as exc:
        logging.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


