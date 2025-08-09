#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
zfs-pbs-backup
==============

Create (and optionally hold) ZFS snapshots and back them up to Proxmox Backup Server,
one archive per dataset. Dry-run by default — pass --execute to perform changes.

Highlights
----------
- Long, descriptive option names (only "zfs" and "pbs" are abbreviated), plus short forms
- Argument groups for PBS and ZFS knobs
- All names/paths are logged with quotes
- A single command runner logs messages/timing and respects dry-run vs read-only
- ZFS interactions live in small, testable helpers; uses parsable options (-H, -p, -o)
- Snapshots are **never created twice**: recursive roots are minimized; descendants are skipped
- Holds-aware snapshot deletion with safety checks
- Orphan cleanup, resume mode, and "exclude empty parents" logic

Typical flow
------------
1) Determine run timestamp (or resume a previous one).
2) Optionally remove orphaned snapshots (ask/true/false/only).
3) Read include-mode property on datasets recursively and build the work plan.
4) Create snapshots (with -r for recursive/children modes), set timestamp property, and clear backed flag.
5) Back up each dataset’s snapshot (pxar) to PBS and mark it backed.
6) Release holds (if used) and destroy snapshots.

Copyright
---------
Public domain / CC0; no warranty. Validate in a non-production environment first.
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# =============================================================================
# Defaults & constants
# =============================================================================

DEFAULT_PROPERTY_INCLUDE = "zfs-pbs-backup:include"  # "true" | "false" | "recursive" | "children"
DEFAULT_PROPERTY_SNAPSHOT_TIMESTAMP = "zfs-pbs-backup:unix_timestamp"  # snapshot property storing unix timestamp
DEFAULT_PROPERTY_SNAPSHOT_DONE = "zfs-pbs-backup:backed_up"  # snapshot property: "true" after successful backup
DEFAULT_SNAPSHOT_PREFIX = "zfs-pbs-backup_"
DEFAULT_SNAPSHOT_HOLD_NAME = "zfs-pbs-backup"

READ_ONLY_ZFS_SUB_COMMANDS = {
    ("zfs", "list"),
    ("zfs", "get"),
    ("zfs", "holds"),
}

REQUIRED_PROGRAMS = [
    "zfs",  # ZFS command-line tool
    "proxmox-backup-client",  # Proxmox Backup Server client
]

ARE_WE_ROOT = os.getuid() == 0  # Check if we are running as root (uid 0)


# =============================================================================
# Small utilities
# =============================================================================

def quote(s: str) -> str:
    """Return s wrapped in double quotes for logging."""
    if '"' in s:
        # If the string contains double quotes, escape them
        s = s.replace('"', '\\"')
    return f'"{s}"'


def infer_read_only(cmd: List[str]) -> bool:
    """
    Heuristic to decide if a command is read-only. Used to allow running discovery
    commands even in dry-run mode, while blocking mutating ones.
    """
    if not cmd:
        return True
    head = tuple(cmd[:2]) if len(cmd) >= 2 else (cmd[0], "")
    if head in READ_ONLY_ZFS_SUB_COMMANDS:
        return True
    if cmd[:1] == ["proxmox-backup-client"] and "backup" not in cmd:
        return True
    return False


def which(program: str) -> Optional[str]:
    """Return an absolute path to prog if found in PATH, else None."""
    from shutil import which as _which
    return _which(program)


def can_execute(program: str) -> bool:
    """
    Check if a program is executable.
    Returns True if the program exists and is executable, False otherwise.
    """
    path = which(program)
    if not path:
        return False
    return os.access(path, os.X_OK)


# =============================================================================
# Command runner with timing & dry-run semantics
# =============================================================================

def run_cmd(
        cmd: List[str],
        *,
        message: Optional[str] = None,
        dry_run: bool = True,
        read_only: Optional[bool] = None,
        env: Optional[Dict[str, str]] = None,
        check: bool = True,
        capture_output: bool = True,
        message_for_return_codes: dict[int, str] = None,
        debug_log: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a command with structured logging and timing.

    - If `message` is provided, log it (prefix with "[dry-run]" if applicable).
    - Ensure the command itself is debug-logged at most once per call, and at least
      once if it's not read-only.
    - In dry-run mode, **read-only commands still execute** (for discovery); mutating
      commands return a fake success result without execution.
    - Always debug-log the elapsed time.

    Returns a subprocess.CompletedProcess (or a synthetic one in dry-run for mutating).
    """
    read_only = infer_read_only(cmd) if read_only is None else read_only

    if message:
        if dry_run and not read_only:
            message = f"[DRY-RUN] {message}"
        if debug_log:
            logging.debug(message)
        else:
            logging.info(message)

    command_string = " ".join(shlex.quote(c) for c in cmd)
    if read_only:
        logging.debug("cmd: %s", command_string)
    else:
        logging.debug("cmd (mutating): %s", command_string)

    if dry_run and not read_only:
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    start = time.perf_counter()
    try:
        # Disable check if message_for_return_codes is provided, as it will handle errors
        if message_for_return_codes is not None:
            check = False
        completed_process = subprocess.run(
            cmd,
            env={**os.environ, **(env or {})},
            check=check,
            capture_output=capture_output,
        )
        # Check return code if message_for_return_codes is provided
        if message_for_return_codes is not None:
            check_command_success(
                cmd,
                completed_process,
                message_for_return_codes,
            )
        return completed_process
    finally:
        elapsed = time.perf_counter() - start
        logging.debug("time: %.3fs for: %s", elapsed, command_string)


def check_command_success(cmd: List[str], completed_process: subprocess.CompletedProcess,
                          message_for_return_codes: dict[int, str]):
    """
    Check if a command was successful based on its return code.
    Raises CalledProcessError for unexpected return codes.
    Returns True if the command was successful (return code 0),
    """
    if completed_process.returncode == 0:
        return

    if completed_process.returncode in message_for_return_codes:
        message = message_for_return_codes[completed_process.returncode]
        logging.error(message)
        sys.exit(1)

    raise subprocess.CalledProcessError(
        completed_process.returncode,
        cmd,
        output=completed_process.stdout,
        stderr=completed_process.stderr,
    )


# =============================================================================
# ZFS helpers (the only place that runs zfs commands)
# =============================================================================

def zfs_list(
        *,
        dataset: Optional[str] = None,
        recursive: bool = False,
        columns=None,
        types=None,
) -> List[List[str]]:
    """
    List ZFS objects with parsable output.

    Equivalent to:
        zfs list -H -p -o <cols> [-r] -t <types> [dataset]

    Returns a list of rows; each row is a list of column strings.
    """
    if columns is None:
        columns = ["name"]
    if types is None:
        types = ["filesystem"]
    cmd = ["zfs", "list", "-H", "-p", "-o", ",".join(columns)]
    if recursive:
        cmd.append("-r")
    if types:
        cmd += ["-t", ",".join(types)]
    if dataset:
        cmd.append(dataset)
    completed_process = run_cmd(cmd, message=None, dry_run=False, read_only=True, message_for_return_codes={
        1: f"Dataset {quote(dataset)} does not exist or is not a ZFS {"/".join(types)}."})
    output = completed_process.stdout.decode().splitlines()
    return [line.split("\t") for line in output if line.strip()]


def zfs_dataset_exists(
        dataset: str,
        *,
        types: Optional[List[str]] = None,
) -> bool:
    """
    Check if a ZFS dataset exists and is of the specified type(s).

    Equivalent to:
        zfs list -H -p -o name -t <types> <dataset>

    Returns True if the dataset exists and matches the type(s), False otherwise.
    """
    if types is None:
        types = ["filesystem", "snapshot"]
    cmd = ["zfs", "list", "-H", "-p", "-o", "name"]
    if types:
        cmd += ["-t", ",".join(types)]
    cmd.append(dataset)
    try:
        completed_process = run_cmd(cmd, dry_run=False, read_only=True, check=False)
        return bool(completed_process.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def check_zfs_datasets_exist(
        datasets: List[str],
        *,
        types: Optional[List[str]] = None,
        check_permission: bool = True,
        cmd: list[str] = None,
):
    """
    Check if all ZFS datasets exist and are of the specified type(s).
    Raises an exception if any dataset does not exist or is of the wrong type.
    """
    if types is None:
        types = ["filesystem", "snapshot"]
    for dataset in datasets:
        if not zfs_dataset_exists(dataset, types=types):
            logging.error(f"Dataset {quote(dataset)} does not exist or is not a ZFS {'/'.join(types)}.")
            sys.exit(1)
    if check_permission and not ARE_WE_ROOT:
        if cmd:
            logging.error(f"Command: {quote(' '.join(cmd))}")
        logging.error("This operation requires root privileges (uid 0) or ZFS permissions to be set up correctly.")
        sys.exit(1)


def zfs_get(
        properties: List[str],
        target: str,
        *,
        source_order: str = "local,received,default,inherited",
) -> Dict[str, str]:
    """
    Get ZFS properties for dataset/snapshot with parsable output.

    Equivalent to:
        zfs get -H -p -o property,value,source -s <sources> <props> <target>

    Returns {property: value}.
    """
    cmd = [
        "zfs", "get", "-H", "-p",
        "-o", "property,value,source",
        "-s", source_order,
        ",".join(properties),
        target,
    ]
    completed_process = run_cmd(cmd, dry_run=False, read_only=True)
    result: Dict[str, str] = {}
    for line in completed_process.stdout.decode().splitlines():
        if not line.strip():
            continue
        key, value, _source = line.split("\t", 2)
        result[key] = value
    return result


def zfs_set(key: str, value: str, target: str, *, dry_run: bool):
    """Set a single ZFS property."""
    cmd = ["zfs", "set", f"{key}={value}", target]
    completed_process = run_cmd(
        cmd,
        message=f"Set ZFS property {quote(key)}={quote(value)} on {quote(target)}",
        dry_run=dry_run,
        read_only=False,
        debug_log=True,
        check=False
    )
    # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist([target], cmd=cmd)
        raise subprocess.CalledProcessError(
            completed_process.returncode,
            cmd,
            output=completed_process.stdout,
            stderr=completed_process.stderr,
        )


def zfs_snapshot_create(
        datasets: List[str],
        snapshot_name: str,
        *,
        recursive: bool,
        hold: bool,
        hold_name: str,
        dry_run: bool,
):
    """
    Create snapshots (optionally recursive) and optionally apply a hold.
    """
    # Create snapshots
    cmd = ["zfs", "snapshot"]
    if recursive:
        cmd.append("-r")
    cmd += [f"{dataset}@{snapshot_name}" for dataset in datasets]
    completed_process = run_cmd(cmd, message=f"Create snapshot(s) {quote(snapshot_name)}", dry_run=dry_run,
                                read_only=False, check=False)
    # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(datasets, cmd=cmd, types=["filesystem"])
        raise subprocess.CalledProcessError(
            completed_process.returncode,
            cmd,
            output=completed_process.stdout,
            stderr=completed_process.stderr,
        )

    # Hold snapshots (optional)
    if hold:
        cmd = ["zfs", "hold"]
        if recursive:
            cmd.append("-r")
        cmd += [hold_name] + [f"{dataset}@{snapshot_name}" for dataset in datasets]
        completed_process = run_cmd(
            cmd,
            message=f"Hold snapshot(s) {quote(snapshot_name)} with hold name {quote(hold_name)}",
            dry_run=dry_run,
            read_only=False,
            check=False
        )
        # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
        if completed_process.returncode != 0:
            check_zfs_datasets_exist([f"{dataset}@{snapshot_name}" for dataset in datasets], cmd=cmd,
                                     types=["snapshot"])
            raise subprocess.CalledProcessError(
                completed_process.returncode,
                cmd,
                output=completed_process.stdout,
                stderr=completed_process.stderr,
            )


def zfs_holds(snapshot_name: str) -> List[str]:
    """
    List holds on a snapshot.

    Equivalent to:
        zfs holds -H <snapshot>

    Returns a list of hold tags.
    """
    cmd = ["zfs", "holds", "-H", snapshot_name]
    try:
        completed_process = run_cmd(cmd, dry_run=False, read_only=True)
        holds = []
        for line in completed_process.stdout.decode().splitlines():
            if not line.strip():
                continue
            # <snapshot>\t<tag>\t<timestamp>
            _snap, tag, _ts = line.split("\t", 2)
            holds.append(tag)
        return holds
    except subprocess.CalledProcessError:
        # Snapshot may not exist
        return []


def zfs_release_hold(snapshot_name: str, hold_name: str, *, dry_run: bool):
    """Release a hold on a snapshot."""
    cmd = ["zfs", "release", hold_name, snapshot_name]
    completed_process = run_cmd(
        cmd,
        message=f"Release hold {quote(hold_name)} on {quote(snapshot_name)}",
        dry_run=dry_run,
        read_only=False,
        check=False,
    )
    # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist([snapshot_name], cmd=cmd, types=["snapshot"])
        raise subprocess.CalledProcessError(
            completed_process.returncode,
            cmd,
            output=completed_process.stdout,
            stderr=completed_process.stderr,
        )


def zfs_destroy_snapshot(snapshot_name: str, *, dry_run: bool):
    """Destroy a snapshot."""
    cmd = ["zfs", "destroy", snapshot_name]
    completed_process = run_cmd(
        cmd,
        message=f"Destroy snapshot {quote(snapshot_name)}",
        dry_run=dry_run,
        read_only=False,
        check=False,
    )
    # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist([snapshot_name], cmd=cmd, types=["snapshot"])
        raise subprocess.CalledProcessError(
            completed_process.returncode,
            cmd,
            output=completed_process.stdout,
            stderr=completed_process.stderr,
        )


def get_mountpoints_recursively(root_dataset: str) -> Dict[str, str]:
    """
    Return {dataset: mountpoint} for root and all descendant filesystems.
    """
    rows = zfs_list(
        dataset=root_dataset,
        recursive=True,
        columns=["name", "mountpoint"],
        types=["filesystem"],
    )
    return {name: mountpoint for name, mountpoint in rows}


def list_snapshots_for_dataset(dataset: str, prefix: str) -> List[str]:
    """
    Return full snapshot names for this dataset that start with the given prefix.
    e.g. "pool/data@zfs-pbs-backup_1699999999"
    """
    rows = zfs_list(
        dataset=dataset,
        recursive=False,
        columns=["name"],
        types=["snapshot"],
    )
    snapshots = [row[0] for row in rows if row]
    full_prefix = f"{dataset}@{prefix}"
    return [snapshot for snapshot in snapshots if snapshot.startswith(full_prefix)]


def snapshot_path_on_disk(dataset_mountpoint: str, snapshot_name: str) -> Path:
    """
    Translate a dataset snapshot to its on-disk directory:

        <mountpoint>/.zfs/snapshot/<snapshot_name>

    Note: snapshot_name is the portion after "@", e.g. "zfs-pbs-backup_1699999999".
    """
    return Path(dataset_mountpoint) / ".zfs" / "snapshot" / snapshot_name


# =============================================================================
# Holds-aware destroy helper
# =============================================================================

def destroy_snapshot_helper(
        dataset: str,
        snapshot_name: str,
        *,
        holding_enabled: bool,
        our_hold_name: str,
        dry_run: bool,
):
    """
    Destroy a snapshot safely:
      - If no holds exist, destroy it.
      - If holds exist and the only hold equals our hold name and holding is enabled,
        release that hold and destroy.
      - Otherwise, warn and skip (external holds interfere).
    """
    snapshot = f"{dataset}@{snapshot_name}"
    holds = zfs_holds(snapshot)
    if not holds:
        zfs_destroy_snapshot(snapshot, dry_run=dry_run)
        return

    if holding_enabled and set(holds) == {our_hold_name}:
        zfs_release_hold(snapshot, our_hold_name, dry_run=dry_run)
        zfs_destroy_snapshot(snapshot, dry_run=dry_run)
        return

    logging.warning(
        "Skip destroying snapshot %s: external holds present (%s) or holding disabled.",
        quote(snapshot), ", ".join(quote(hold) for hold in holds) if holds else "none",
    )


# =============================================================================
# Planning & dataset selection
# =============================================================================

@dataclass
class DatasetPlan:
    dataset: str
    mountpoint: str
    include_mode: str  # "true" | "false" | "recursive" | "children"
    recursive_for_snapshot: bool  # True if -r snapshotting is intended from this dataset
    process_self: bool  # Whether to back up this dataset itself


def is_parent_empty_excluding_child_mounts(parent_mnt: str, child_mounts: Iterable[str]) -> bool:
    """
    Consider a parent dataset "empty" if its mountpoint contains no files nor directories
    *other than* the directories that are mountpoints for its child datasets.
    This only checks immediate entries (shallow scan).
    """
    parent = Path(parent_mnt)
    try:
        entries = list(parent.iterdir())
    except Exception:
        # Permission or transient issue: treat as not empty (conservative)
        return False

    child_mounts_set = {os.path.abspath(p) for p in child_mounts}
    for entry in entries:
        absolute_entry = os.path.abspath(str(entry))
        if absolute_entry in child_mounts_set:
            continue
        # Any other file/dir => not empty
        return False
    return True


def collect_datasets_to_backup(
        root_datasets: List[str],
        *,
        property_include: str,
        exclude_empty_parents: bool,
) -> List[DatasetPlan]:
    """
    Inspect include modes (true/false/recursive/children) and construct a plan.

    - "recursive" and "children" trigger -r snapshotting.
    - "children" excludes processing the parent itself (children only).
    - Optionally skip empty parents (with children) for "true"/"recursive".
    """
    plans: List[DatasetPlan] = []

    for root_dataset in root_datasets:
        mountpoint_by_dataset = get_mountpoints_recursively(root_dataset)
        # dataset -> include mode
        include_modes: Dict[str, str] = {}
        for dataset in mountpoint_by_dataset.keys():
            include_mode = zfs_get([property_include], dataset).get(property_include, "").strip().lower()
            if include_mode == "":
                include_mode = "false"
            if include_mode not in {"true", "false", "recursive", "children"}:
                logging.warning(
                    "Dataset %s has unknown %s=%s; treating as false.",
                    quote(dataset), quote(property_include), quote(include_mode)
                )
                include_mode = "false"
            include_modes[dataset] = include_mode

        # Precompute child mountpoints for empty-parent checks
        children_by_parent: Dict[str, List[str]] = {dataset: [] for dataset in mountpoint_by_dataset.keys()}
        for dataset in mountpoint_by_dataset.keys():
            for other in mountpoint_by_dataset.keys():
                if other != dataset and other.startswith(dataset + "/"):
                    children_by_parent[dataset].append(mountpoint_by_dataset[other])

        for dataset, mountpoint in mountpoint_by_dataset.items():
            include_mode = include_modes[dataset]
            recursive_flag = include_mode in {"recursive", "children"}
            process_self = include_mode in {"true", "recursive"}

            if process_self and exclude_empty_parents:
                child_mounts = children_by_parent.get(dataset, [])
                if child_mounts and is_parent_empty_excluding_child_mounts(mountpoint, child_mounts):
                    process_self = False
                    logging.info("Skip empty parent dataset %s at %s", quote(dataset), quote(mountpoint))

            if include_mode != "false":
                plans.append(DatasetPlan(
                    dataset=dataset,
                    mountpoint=mountpoint,
                    include_mode=include_mode,
                    recursive_for_snapshot=recursive_flag,
                    process_self=process_self,
                ))

    return plans


# =============================================================================
# Resume & orphan discovery
# =============================================================================

def find_resume_timestamp(
        dataset_plans: List[DatasetPlan],
        *,
        snapshot_prefix: str,
        property_snapshot_timestamp: str,
) -> Optional[str]:
    """
    Find the newest unix timestamp among snapshots that match the prefix.
    Prefer the stored property; fall back to parsing the name suffix.
    """
    timestamp_newest: Optional[str] = None
    for plan in dataset_plans:
        snapshots = list_snapshots_for_dataset(plan.dataset, snapshot_prefix)
        for snapshot in snapshots:
            snapshot_name = snapshot.split("@", 1)[1]
            properties = zfs_get([property_snapshot_timestamp], snapshot)
            timestamp = properties.get(property_snapshot_timestamp, "").strip()
            if timestamp.isdigit():
                if timestamp_newest is None or int(timestamp) > int(timestamp_newest):
                    timestamp_newest = timestamp
            else:
                if snapshot_name.startswith(snapshot_prefix):
                    suffix = snapshot_name[len(snapshot_prefix):]
                    if suffix.isdigit() and (timestamp_newest is None or int(suffix) > int(timestamp_newest)):
                        timestamp_newest = suffix
    return timestamp_newest


def find_orphan_snapshots(
        dataset_plans: List[DatasetPlan],
        *,
        snapshot_prefix: str,
        timestamp_current: str,
        property_snapshot_timestamp: str,
) -> List[Tuple[str, str]]:
    """
    Return a list of (dataset, snapshot_name) for snapshots that match our prefix but
    do not belong to the current run timestamp.
    """
    orphans: List[Tuple[str, str]] = []
    for plan in dataset_plans:
        snapshots = list_snapshots_for_dataset(plan.dataset, snapshot_prefix)
        for snapshot in snapshots:
            dataset, snapshot_name = snapshot.split("@", 1)
            properties = zfs_get([property_snapshot_timestamp], snapshot)
            timestamp = properties.get(property_snapshot_timestamp, "").strip()
            if not timestamp.isdigit():
                if snapshot_name.startswith(snapshot_prefix):
                    suffix = snapshot_name[len(snapshot_prefix):]
                    timestamp = suffix if suffix.isdigit() else ""
            if timestamp != timestamp_current:
                orphans.append((dataset, snapshot_name))
    return orphans


# =============================================================================
# Proxmox Backup Server helper
# =============================================================================

def pbs_build_repository_string(
        *,
        username: Optional[str],
        token_name: Optional[str],
        server: Optional[str],
        port: Optional[int],
        datastore: Optional[str],
) -> str:
    """
    Build a Proxmox Backup Server repository string.

    Format: [[username@]server[:port]:]datastore
    """
    if not datastore:
        raise ValueError("Datastore must be specified for PBS repository string.")
    repo_parts = []
    if username:
        if token_name:
            repo_parts.append(f"{username}!{token_name}@")
        else:
            repo_parts.append(f"{username}@")
    if server:
        repo_parts.append(f"{server}:")
    if port:
        repo_parts.append(f"{port}:")
    repo_parts.append(datastore)
    return "".join(repo_parts)


def pbs_backup_dataset_snapshot(
        *,
        dataset: str,
        mountpoint: str,
        snapshot_name: str,
        repository: str,
        secret: Optional[str],  # password/token secret; if None, we will prompt only on executing
        namespace: Optional[str],
        backup_id: str,
        backup_time: str,
        archive_name_prefix: Optional[str],
        encryption_password: Optional[str],
        fingerprint: Optional[str],
        dry_run: bool,
) -> None:
    """
    Back up the snapshot directory as a pxar archive using proxmox-backup-client.

    Archive: <dataset with '/' -> '_'>.pxar:<snapshot directory>
    Backup-ID: <backup_id_prefix><dataset>
    """
    snapshot_directory = snapshot_path_on_disk(mountpoint, snapshot_name)
    if not snapshot_directory.exists():
        logging.warning("Skip dataset %s: snapshot directory %s does not exist.",
                        quote(dataset), quote(str(snapshot_directory)))
        return
    elif not snapshot_directory.is_dir():
        logging.warning("Skip dataset %s: snapshot directory %s is not a directory.",
                        quote(dataset), quote(str(snapshot_directory)))
        return
    elif not os.access(snapshot_directory, os.R_OK):
        logging.warning("Skip dataset %s: snapshot directory %s is not readable.",
                        quote(dataset), quote(str(snapshot_directory)))
        return

    dataset_id = dataset.replace("/", "_")
    archive_name = f"{archive_name_prefix or ""}{dataset_id}.pxar:{str(snapshot_directory)}"

    env = {}
    if repository:
        env["REPOSITORY"] = repository
    else:
        logging.error("PBS repository must be specified.")
        sys.exit(1)
    if secret:
        env["PBS_PASSWORD"] = secret
    else:
        logging.error("PBS secret (password or token) must be specified.")
        sys.exit(1)
    if encryption_password:
        env["PBS_ENCRYPTION_PASSWORD"] = encryption_password
    if fingerprint:
        env["PBS_FINGERPRINT"] = fingerprint
        logging.debug("Using PBS fingerprint: %s", quote(fingerprint))

    cmd = [
        "proxmox-backup-client", "backup",
        archive_name,
        "--backup-type", "host",
        "--backup-id", backup_id,
        "--backup-time", backup_time,
    ]
    if namespace:
        cmd += ["--ns", namespace]

    if dry_run:
        cmd.append("--dry-run")

    run_cmd(
        cmd,
        message=f"Back up dataset {quote(dataset)} snapshot {quote(snapshot_name)} "
                f"to PBS repository {quote(repository)} as backup-id {quote(backup_id)}",
        dry_run=dry_run,
        read_only=False,
        env=env,
    )


# =============================================================================
# Snapshot orchestration
# =============================================================================

def _minimize_recursive_roots(recursive_datasets: List[str]) -> List[str]:
    """
    Given a list of datasets intended for recursive (-r) snapshotting, return a
    minimized set where descendants of another recursive root are removed.

    Example:
      ["pool", "pool/data", "pool/data/vm"] -> ["pool"]
    """
    root_datasets = sorted(set(recursive_datasets))
    minimized: List[str] = []
    for dataset in root_datasets:
        if not any(dataset.startswith(parent + "/") for parent in minimized):
            minimized.append(dataset)
    return minimized


def create_snapshots_for_plans(
        plans: List[DatasetPlan],
        *,
        snapshot_name: str,
        hold_snapshots: bool,
        hold_name: str,
        dry_run: bool,
):
    """
    Create snapshots efficiently while ensuring each dataset gets snapshotted at most once.

    Strategy:
      1) Gather datasets that requested recursive snapshotting and **minimize** them so
         that child datasets under a recursive root are not listed separately.
      2) Snapshot those roots with -r.
      3) Gather non-recursive datasets and **exclude** any that are descendants of any
         recursive root (they're already covered by step 2).
      4) Snapshot the remaining non-recursive datasets without -r (can be batched).
    """
    recursive_roots = _minimize_recursive_roots([p.dataset for p in plans if p.recursive_for_snapshot])

    # Step 2: snapshot recursive roots
    for root in recursive_roots:
        zfs_snapshot_create([root], snapshot_name, recursive=True, hold=hold_snapshots, hold_name=hold_name,
                            dry_run=dry_run)

    # Step 3: compute a descendants-covered set
    def covered_by_recursive(dataset: str) -> bool:
        return any(
            dataset.startswith(recursive_root + "/") or dataset == recursive_root for recursive_root in recursive_roots)

    non_recursive_candidates = [plan.dataset for plan in plans if not plan.recursive_for_snapshot]
    non_recursive_targets = [dataset for dataset in non_recursive_candidates if not covered_by_recursive(dataset)]

    # Step 4: snapshot the remaining non-recursive datasets in one go (if any)
    if non_recursive_targets:
        zfs_snapshot_create(non_recursive_targets, snapshot_name, recursive=False, hold=hold_snapshots,
                            hold_name=hold_name,
                            dry_run=dry_run)


def mark_snapshot_timestamp_and_reset_done(
        plans: List[DatasetPlan],
        *,
        dataset_filter_self_only: bool,
        snapshot_name: str,
        property_snapshot_timestamp: str,
        property_snapshot_done: str,
        timestamp: str,
        dry_run: bool,
):
    """
    Stamp the snapshot with the run timestamp and clear the "backed_up" flag.
    Only applies to datasets that we will actually back up when dataset_filter_self_only=True.
    """
    for plan in plans:
        if dataset_filter_self_only and not plan.process_self:
            continue
        snapshot = f"{plan.dataset}@{snapshot_name}"
        zfs_set(property_snapshot_timestamp, timestamp, snapshot, dry_run=dry_run)
        zfs_set(property_snapshot_done, "false", snapshot, dry_run=dry_run)


def filter_plans_for_existing_unbacked(
        plans: List[DatasetPlan],
        *,
        snapshot_name: str,
        property_snapshot_done: str,
) -> List[DatasetPlan]:
    """
    Keep only datasets where the snapshot exists and is not yet marked as backed up.
    """
    selected: List[DatasetPlan] = []
    for plan in plans:
        snapshot = f"{plan.dataset}@{snapshot_name}"
        try:
            properties = zfs_get([property_snapshot_done], snapshot)
        except subprocess.CalledProcessError:
            # Snapshot missing
            continue
        done = properties.get(property_snapshot_done, "").strip().lower() == "true"
        if not done and plan.process_self:
            selected.append(plan)
    return selected


def cleanup_orphans_if_any(
        plans: List[DatasetPlan],
        *,
        snapshot_prefix: str,
        timestamp_current: str,
        property_snapshot_timestamp: str,
        remove_orphans: str,  # "true" | "false" | "ask" | "only"
        holding_enabled: bool,
        hold_name: str,
        dry_run: bool,
) -> None:
    """Find and optionally remove orphan snapshots from previous runs."""
    orphans = find_orphan_snapshots(plans, snapshot_prefix=snapshot_prefix, timestamp_current=timestamp_current,
                                    property_snapshot_timestamp=property_snapshot_timestamp)
    if not orphans:
        return

    if remove_orphans == "false":
        logging.warning(
            "Found %d orphaned snapshot(s) with prefix %s; not removing (--remove-orphans=false).",
            len(orphans), quote(snapshot_prefix),
        )
        return

    if remove_orphans == "ask":
        logging.warning(
            "Found %d orphaned snapshot(s) with prefix %s. There might be another instance using them.",
            len(orphans), quote(snapshot_prefix),
        )
        answer = input("Remove orphaned snapshots now? [y/N]: ").strip().lower()
        if answer != "y":
            logging.info("Skipping orphan removal.")
            return

    if remove_orphans not in ["true", "ask", "only"]:
        logging.error(
            "Invalid --remove-orphans value: %s; expected 'true', 'false', 'ask', or 'only'.",
            quote(remove_orphans)
        )
        sys.exit(1)

    for dataset, snapshot_name in orphans:
        destroy_snapshot_helper(
            dataset,
            snapshot_name,
            holding_enabled=holding_enabled,
            our_hold_name=hold_name,
            dry_run=dry_run,
        )


# =============================================================================
# CLI construction
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="zfs-pbs-backup",
        description="ZFS → Proxmox Backup Server backup helper (dry-run by default; pass --execute to make changes).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Positional: at least one root dataset
    p.add_argument("datasets", nargs="+", help="ZFS datasets to consider (roots).")

    # PBS repository options (group)
    g_pbs_r = p.add_argument_group("PBS repository options")
    ## PBS repository options string
    g_pbs_r.add_argument("-R", "--pbs-repository",
                         help="PBS repository (e.g. 'user@pbs!token@host:store'). Takes precedence over other PBS options.")
    ## PBS repository options secret
    g_pbs_r.add_argument("-P", "--pbs-secret",
                         help="PBS password or API token secret. If omitted, you will be prompted securely when needed.")
    ## PBS repository options
    g_pbs_r.add_argument("--pbs-username", help="PBS username for authentication (e.g. 'user@pbs!token').")
    g_pbs_r.add_argument("--pbs-server", help="PBS hostname or IP address (e.g. 'host').")
    g_pbs_r.add_argument("--pbs-port", type=int, help="PBS port (e.g. '8007').")
    g_pbs_r.add_argument("--pbs-datastore", help="PBS datastore (e.g. 'store').")
    g_pbs_r.add_argument("--pbs-fingerprint", help="PBS server fingerprint (optional; used for verification).")

    # PBS backup options (group)
    g_pbs_b = p.add_argument_group("PBS backup options")
    g_pbs_b.add_argument("-K", "--pbs-encryption-password", help="PBS encryption password (empty disables encryption).")
    g_pbs_b.add_argument("-N", "--pbs-namespace", help="PBS namespace.")
    g_pbs_b.add_argument("-B", "--pbs-backup-id", default=socket.gethostname(),
                         help="ID for the backup (defaults to local hostname).")
    g_pbs_b.add_argument("--pbs-archive-name-prefix",
                         help="Prefix added to the archive name (archive name = 'prefix + <dataset with '/' -> '_'>.pxar').")

    # ZFS options (group)
    g_zfs = p.add_argument_group("ZFS options")
    g_zfs.add_argument("-H", "--hold-snapshots", action=argparse.BooleanOptionalAction, default=True,
                       help="Hold temporary snapshots until they are backed up.")
    g_zfs.add_argument("-X", "--exclude-empty-parents", action=argparse.BooleanOptionalAction, default=True,
                       help="If a dataset has children and is empty itself, skip backing up the parent dataset.")
    g_zfs.add_argument("-O", "--remove-orphans", choices=["true", "false", "ask", "only"], default="ask",
                       help="Remove orphaned snapshots whose timestamp does not match the current run. Option 'only' will only remove orphans and not create new snapshots or backups.")

    # ZFS label options (group)
    g_zfs_l = p.add_argument_group("ZFS label options")
    g_zfs_l.add_argument("--zfs-snapshot-prefix", default=DEFAULT_SNAPSHOT_PREFIX,
                         help="Prefix for snapshot names (final name is <prefix><timestamp>).")
    g_zfs_l.add_argument("--zfs-hold-name", default=DEFAULT_SNAPSHOT_HOLD_NAME,
                         help="Hold name to apply to temporary snapshots.")
    g_zfs_l.add_argument("--zfs-include-property", default=DEFAULT_PROPERTY_INCLUDE,
                         help="ZFS dataset property controlling include mode: true/false/recursive/children.")
    g_zfs_l.add_argument("--zfs-snapshot-timestamp-property", default=DEFAULT_PROPERTY_SNAPSHOT_TIMESTAMP,
                         help="ZFS snapshot property storing the unix timestamp for this run.")
    g_zfs_l.add_argument("--zfs-snapshot-done-property", default=DEFAULT_PROPERTY_SNAPSHOT_DONE,
                         help="ZFS snapshot property set to 'true' after a successful backup.")

    # Resume & run behavior
    p.add_argument("-C", "--resume", action=argparse.BooleanOptionalAction, default=False,
                   help="Resume using the newest existing timestamp; skip snapshot creation if found.")

    # Logging
    p.add_argument("-v", "--verbose", action=argparse.BooleanOptionalAction, default=False,
                   help="Verbose logging (INFO).")
    p.add_argument("-d", "--debug", action=argparse.BooleanOptionalAction, default=False,
                   help="Debug logging (DEBUG).")
    p.add_argument("-q", "--quiet", action=argparse.BooleanOptionalAction, default=False,
                   help="Quiet mode (suppress logs).")

    # Anti dry-run: explicit execute flag
    p.add_argument("-E", "--execute", action=argparse.BooleanOptionalAction, default=False,
                   help="Perform changes. Without this, the script runs in dry-run mode.")

    return p


def configure_logging(verbose: bool, debug: bool, quiet: bool):
    """Configure root logger according to verbosity switches."""
    if quiet:
        level = logging.CRITICAL + 1
    elif debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")


def ensure_tools():
    """Abort early if required CLI tools are not available."""
    missing = [program for program in REQUIRED_PROGRAMS if which(program) is None]
    if missing:
        logging.error("Missing required tools: %s", ", ".join(missing))
        sys.exit(2)

    unaccessible = [program for program in REQUIRED_PROGRAMS if not can_execute(program)]
    if unaccessible:
        logging.error("Required tools are not executable: %s", ", ".join(unaccessible))
        sys.exit(2)


def check_permissions():
    """Check if we can execute ZFS commands."""
    if ARE_WE_ROOT:
        logging.info("Running as root; ensure you trust this script and its source.")
    else:
        logging.warning(
            "Not running as root; ensure you have sufficient permissions to create/destroy/hold/mount/release/set ZFS snapshots.")


def secure_prompt(prompt_text: str) -> str:
    """Prompt for a secret without echoing."""
    import getpass
    return getpass.getpass(prompt_text)


# =============================================================================
# Main
# =============================================================================

def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.verbose, args.debug, args.quiet)

    # Debug logging of CLI options
    if args.debug:
        logging.debug("CLI options / arguments:")
        options = vars(args)
        max_length = max(len(key) for key in options)
        for option in sorted(options):
            logging.debug("  %-*s : %r", max_length, option, options[option])

    ensure_tools()
    check_permissions()

    # Prompt for secret only when actually needed (execute or resume)
    pbs_secret = args.pbs_secret
    if (args.execute or args.resume) and not pbs_secret:
        try:
            pbs_secret = secure_prompt("PBS password or API token secret (leave empty to skip): ")
        except (EOFError, KeyboardInterrupt):
            pbs_secret = ""

    # Build the plan
    plans = collect_datasets_to_backup(
        root_datasets=args.datasets,
        property_include=args.zfs_include_property,
        exclude_empty_parents=args.exclude_empty_parents,
    )
    if not plans:
        logging.warning("No datasets selected. Check %s property values.", quote(args.zfs_include_property))
        return 0

    # Determine snapshot name
    timestamp_now = str(int(time.time()))
    if args.resume:
        timestamp_newest = find_resume_timestamp(plans, snapshot_prefix=args.zfs_snapshot_prefix,
                                                 property_snapshot_timestamp=args.zfs_snapshot_timestamp_property)
        if not timestamp_newest:
            logging.warning("Resume requested, but no suitable existing timestamp found. Aborting.")
            return 1
        snapshot_name = f"{args.zfs_snapshot_prefix}{timestamp_newest}"
        timestamp_current = timestamp_newest
        logging.warning("Resuming: skipping snapshot creation and using existing timestamp %s (snapshot %s).",
                        timestamp_newest, quote(snapshot_name))
        # Only process datasets that have this snapshot and are not marked done
        plans = filter_plans_for_existing_unbacked(plans, snapshot_name=snapshot_name,
                                                   property_snapshot_done=args.zfs_snapshot_done_property)
        if not plans:
            logging.info("Nothing to do after resume filtering.")
            return 0
    else:
        snapshot_name = f"{args.zfs_snapshot_prefix}{timestamp_now}"
        timestamp_current = timestamp_now

    # Orphan cleanup (ask/true/false/only)
    cleanup_orphans_if_any(
        plans,
        snapshot_prefix=args.zfs_snapshot_prefix,
        timestamp_current=timestamp_current,
        property_snapshot_timestamp=args.zfs_snapshot_timestamp_property,
        remove_orphans=args.remove_orphans,
        holding_enabled=args.hold_snapshots,
        hold_name=args.zfs_hold_name,
        dry_run=not args.execute,
    )

    # If we are only removing orphans, we can exit early
    if args.remove_orphans == "only":
        logging.info("Orphan cleanup completed. Exiting as per --remove-orphans=only.")
        return 0

    # Create snapshots (unless resuming)
    if not args.resume:
        create_snapshots_for_plans(
            plans,
            snapshot_name=snapshot_name,
            hold_snapshots=args.hold_snapshots,
            hold_name=args.zfs_hold_name,
            dry_run=not args.execute,
        )
        # Stamp timestamp and clear done flag on snapshots we will actually back up
        mark_snapshot_timestamp_and_reset_done(
            plans,
            dataset_filter_self_only=True,
            snapshot_name=snapshot_name,
            property_snapshot_timestamp=args.zfs_snapshot_timestamp_property,
            property_snapshot_done=args.zfs_snapshot_done_property,
            timestamp=timestamp_current,
            dry_run=not args.execute,
        )
    else:
        logging.info("Snapshot creation skipped due to resume mode.")

    # Filter to actionable items (existing snapshot and not done)
    if not args.resume:
        plans = filter_plans_for_existing_unbacked(plans, snapshot_name=snapshot_name,
                                                   property_snapshot_done=args.zfs_snapshot_done_property)
        if not plans:
            if args.execute:
                logging.info("Nothing to back up (already done?).")
            else:
                logging.info("Nothing to back up (dry-run mode).")
            return 0

    # Build the PBS repository string
    pbs_repository = args.pbs_repository
    if not pbs_repository:
        pbs_repository = pbs_build_repository_string(
            username=args.pbs_username,
            token_name=None,  # PBS token name is not supported in this version (it can be part of the username)
            server=args.pbs_server,
            port=args.pbs_port,
            datastore=args.pbs_datastore,
        )

    # Backup loop
    for plan in plans:
        pbs_backup_dataset_snapshot(
            dataset=plan.dataset,
            mountpoint=plan.mountpoint,
            snapshot_name=snapshot_name,
            repository=pbs_repository,
            secret=pbs_secret if pbs_secret else None,
            namespace=args.pbs_namespace if args.pbs_namespace else None,
            backup_id=args.pbs_backup_id if args.pbs_backup_id else socket.gethostname(),
            backup_time=timestamp_current,
            archive_name_prefix=args.pbs_archive_name_prefix if args.pbs_archive_name_prefix else None,
            encryption_password=args.pbs_encryption_password if args.pbs_encryption_password else None,
            fingerprint=args.pbs_fingerprint if args.pbs_fingerprint else None,
            dry_run=not args.execute,
        )
        # Mark as backed up
        snapshot = f"{plan.dataset}@{snapshot_name}"
        zfs_set(args.zfs_snapshot_done_property, "true", snapshot, dry_run=not args.execute)

    # Tear-down: release holds (if ours) and destroy snapshots
    for plan in plans:
        destroy_snapshot_helper(
            plan.dataset,
            snapshot_name,
            holding_enabled=args.hold_snapshots,
            our_hold_name=args.zfs_hold_name,
            dry_run=not args.execute,
        )

    return 0


# Entry point
if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)
