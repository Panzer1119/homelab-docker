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
2) Optionally remove orphaned snapshots (ask/true/false/only/force-release).
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


def s(n: int | list) -> str:
    """
    Return an empty string if n is 1, otherwise return 's'
    """
    if isinstance(n, list):
        n = len(n)
    return "" if n == 1 else "s"


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
        completed_process: subprocess.CompletedProcess = subprocess.run(
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
# ZFS wrappers (the only place that runs zfs commands)
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
    cmd: List[str] = ["zfs", "list", "-H", "-p", "-o", ",".join(columns)]
    if recursive:
        cmd.append("-r")
    if types:
        cmd += ["-t", ",".join(types)]
    if dataset:
        cmd.append(dataset)
    completed_process: subprocess.CompletedProcess = run_cmd(cmd, message=None, dry_run=False, read_only=True,
                                                             message_for_return_codes={
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
    cmd: List[str] = ["zfs", "list", "-H", "-p", "-o", "name"]
    if types:
        cmd += ["-t", ",".join(types)]
    cmd.append(dataset)
    try:
        completed_process: subprocess.CompletedProcess = run_cmd(cmd, dry_run=False, read_only=True, check=False)
        return bool(completed_process.stdout.strip())
    except subprocess.CalledProcessError:
        return False


def check_zfs_datasets_exist(
        datasets: List[str],
        completed_process: subprocess.CompletedProcess,
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
    raise subprocess.CalledProcessError(
        completed_process.returncode,
        cmd,
        output=completed_process.stdout,
        stderr=completed_process.stderr,
    )


def zfs_get(
        datasets: List[str],
        properties: List[str],
        *,
        source_order: List[str] = None,
) -> Dict[str, Dict[str, str]]:
    """
    Get ZFS properties for datasets with parsable output.
    """
    if source_order is None:
        source_order = ["local", "received", "default", "inherited"]
    # If no datasets to get properties from, we're done
    if not datasets:
        logging.info("No datasets to get properties from.")
        return {}
    # Make datasets unique
    datasets: List[str] = list(set(datasets))
    # Build the command
    cmd: List[str] = [
        "zfs", "get", "-H", "-p",
        "-o", "name,property,value,source",
        "-s", ",".join(source_order),
        ",".join(properties),
    ]
    cmd += datasets
    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(cmd, dry_run=False, read_only=True, check=False)
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(datasets, completed_process, cmd=cmd)

    properties_by_dataset: Dict[str, Dict[str, str]] = {}
    for line in completed_process.stdout.decode().splitlines():
        if not line.strip():
            continue
        dataset, key, value, source = line.split("\t", 3)
        if dataset not in properties_by_dataset:
            properties_by_dataset[dataset] = {}
        properties_by_dataset[dataset][key] = value
    return properties_by_dataset


def zfs_set(datasets: List[str], properties: Dict[str, str], *, dry_run: bool) -> None:
    """
    Set a ZFS property on datasets.
    """
    # If no datasets to set properties on, we're done
    if not datasets:
        if dry_run:
            logging.info("No datasets to set properties on in dry-run mode.")
        else:
            logging.info("No datasets to set properties on.")
        return
    # Make datasets unique
    datasets: List[str] = list(set(datasets))
    # Build the command
    cmd: List[str] = ["zfs", "set"]
    cmd += [f"{key}={value}" for key, value in properties.items()]
    cmd += datasets

    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message=f"Set ZFS properties {", ".join([f"{quote(key)}={quote(value)}" for key, value in properties.items()])} on {len(datasets)} dataset{s(datasets)}",
        dry_run=dry_run,
        read_only=False,
        debug_log=True,
        check=False
    )
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(datasets, completed_process, cmd=cmd)


def zfs_create_snapshots(datasets: List[str], snapshot_name: str, *, recursive: bool, dry_run: bool) -> List[str]:
    """
    Create ZFS snapshots.
    """
    # If no datasets to snapshot, we're done
    if not datasets:
        if dry_run:
            logging.info("No datasets to snapshot in dry-run mode.")
        else:
            logging.info("No datasets to snapshot.")
        return []
    # Make datasets unique
    datasets: List[str] = list(set(datasets))
    # Generate snapshots
    snapshots: List[str] = [f"{dataset}@{snapshot_name}" for dataset in datasets]
    # Build the command
    cmd: List[str] = ["zfs", "snapshot"]
    if recursive:
        cmd.append("-r")
    cmd += snapshots

    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message=f"Create {len(snapshots)} snapshot{s(snapshots)}" + " recursively" if recursive else "",
        dry_run=dry_run,
        read_only=False,
        check=False,
    )
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(datasets, completed_process, cmd=cmd, types=["filesystem"])

    # Return the created snapshots
    return snapshots


def zfs_hold_snapshots(snapshots: List[str], hold_name: str, *, recursive: bool, dry_run: bool) -> None:
    """
    Holds multiple ZFS snapshots with a given hold name.
    """
    # If no snapshots to hold, we're done
    if not snapshots:
        if dry_run:
            logging.info("No snapshots to hold in dry-run mode.")
        else:
            logging.info("No snapshots to hold.")
        return
    # Make snapshots unique
    snapshots: List[str] = list(set(snapshots))
    # Build the command
    cmd: List[str] = ["zfs", "hold"]
    if recursive:
        cmd.append("-r")
    cmd += [hold_name]
    cmd += snapshots

    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message=f"Hold {len(snapshots)} snapshot{s(snapshots)} with hold name {quote(hold_name)}" + " recursively" if recursive else "",
        dry_run=dry_run,
        read_only=False,
        check=False,
    )
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(snapshots, completed_process, cmd=cmd, types=["snapshot"])


def zfs_holds(snapshots: List[str], *, recursive: bool, dry_run: bool) -> Dict[str, List[str]]:
    """
    List holds on ZFS snapshots.
    """
    # If no snapshots to check, we're done
    if not snapshots:
        if dry_run:
            logging.info("No snapshots to check holds in dry-run mode.")
        else:
            logging.info("No snapshots to check holds.")
        return {}
    # Make snapshots unique
    snapshots: List[str] = list(set(snapshots))
    # Build the command
    cmd: List[str] = ["zfs", "holds", "-H", "-p"]
    if recursive:
        cmd.append("-r")
    cmd += snapshots

    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(cmd, dry_run=False, read_only=True, check=False)
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(snapshots, completed_process, cmd=cmd, types=["snapshot"])

    # Parse the output
    holds_by_snapshot: Dict[str, List[str]] = {snapshot: [] for snapshot in snapshots}
    for line in completed_process.stdout.decode().splitlines():
        if not line.strip():
            continue
        # <snapshot>\t<tag>\t<timestamp>
        snapshot, tag, timestamp = line.split("\t", 2)
        # if snapshot not in holds_by_snapshot:
        #     holds_by_snapshot[snapshot] = []
        holds_by_snapshot[snapshot].append(tag)
    return holds_by_snapshot


def zfs_release_snapshots(snapshots: List[str], hold_name: str, *, recursive: bool, dry_run: bool) -> None:
    """
    Release a hold on multiple snapshots.
    """
    # If no snapshots to release, we're done
    if not snapshots:
        if dry_run:
            logging.info("No snapshots to release in dry-run mode.")
        else:
            logging.info("No snapshots to release.")
        return
    # Make snapshots unique
    snapshots = list(set(snapshots))
    # Build the command
    cmd: List[str] = ["zfs", "release", hold_name]
    if recursive:
        cmd.append("-r")
    cmd += snapshots

    # Run the command
    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message=f"Release hold {quote(hold_name)} on {len(snapshots)} snapshot{s(snapshots)}" + " recursively" if recursive else "",
        dry_run=dry_run,
        read_only=False,
        check=False,
    )
    # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
    if completed_process.returncode != 0:
        check_zfs_datasets_exist(snapshots, completed_process, cmd=cmd, types=["snapshot"])


def zfs_destroy_snapshots(snapshots: List[str], *, recursive: bool, dry_run: bool) -> None:
    """
    Destroy ZFS snapshots.
    """
    # If no snapshots to destroy, we're done
    if not snapshots:
        if dry_run:
            logging.info("No snapshots to destroy in dry-run mode.")
        else:
            logging.info("No snapshots to destroy.")
        return
    # Make snapshots unique
    snapshots = list(set(snapshots))
    # Check if the snapshots are valid
    if not all("@" in snapshot for snapshot in snapshots):
        logging.error("Abort destroying snapshots: Some snapshots do not contain an '@' character: %s",
                      ", ".join(quote(snapshot) for snapshot in snapshots))
        sys.exit(1)

    # Destroy each snapshot
    for snapshot in snapshots:
        # Build the command
        cmd: List[str] = ["zfs", "destroy"]
        if recursive:
            cmd.append("-r")
        cmd += [snapshot]

        # Run the command
        completed_process: subprocess.CompletedProcess = run_cmd(
            cmd,
            message=f"Destroy snapshot {quote(snapshot)}" + " recursively" if recursive else "",
            dry_run=dry_run,
            read_only=False,
            check=False,
        )
        # If the command failed, it's either because the datasets do not exist or we don't have enough permissions.
        if completed_process.returncode != 0:
            check_zfs_datasets_exist([snapshot], completed_process, cmd=cmd, types=["snapshot"])


# =============================================================================
# ZFS helpers
# =============================================================================


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


def zfs_create_and_hold_snapshots(
        datasets: List[str],
        snapshot_name: str,
        *,
        recursive: bool,
        hold_snapshots: bool,
        hold_name: str,
        dry_run: bool,
):
    """
    Create snapshots (optionally recursive) and optionally apply a hold.
    """
    # Create snapshots
    snapshots: List[str] = zfs_create_snapshots(datasets, snapshot_name, recursive=recursive, dry_run=dry_run)

    # Hold snapshots (optional)
    if hold_snapshots:
        zfs_hold_snapshots(
            snapshots,
            hold_name,
            recursive=recursive,
            dry_run=dry_run,
        )


def zfs_release_and_destroy_snapshots(
        datasets: List[str],
        snapshot_name: str,
        *,
        recursive: bool,
        hold_snapshots: bool,
        hold_name: str,
        dry_run: bool,
        force_release: bool = False,
):
    """
    Release holds (if any) and destroy snapshots (optionally recursive).
    """
    # Generate snapshots
    snapshots: List[str] = [f"{dataset}@{snapshot_name}" for dataset in datasets]
    # Make snapshots unique
    snapshots = list(set(snapshots))

    # Get holds on the snapshots
    holds_by_snapshot: Dict[str, List[str]] = zfs_holds(snapshots, recursive=recursive, dry_run=dry_run)

    # Filter snapshots
    snapshots_to_release_by_hold: Dict[str, List[str]] = {}
    snapshots_to_destroy: List[str] = []
    for snapshot, holds in holds_by_snapshot.items():
        # Skip if snapshot does not contain an at-sign (not a snapshot)
        if "@" not in snapshot:
            logging.warning("Skip destroying %s: not a snapshot (no '@' in name).", quote(snapshot))
            continue
        # If no holds exist, we can destroy the snapshot
        if not holds:
            snapshots_to_destroy.append(snapshot)
            continue
        # If holds exist and the only hold equals our hold name, we can release the hold and destroy
        if (hold_snapshots and set(holds) == {hold_name}) or force_release:
            for hold in holds:
                if not hold in snapshots_to_release_by_hold:
                    snapshots_to_release_by_hold[hold] = []
                snapshots_to_release_by_hold[hold].append(snapshot)
            snapshots_to_destroy.append(snapshot)
            continue
        # Otherwise, we cannot destroy the snapshot (external holds interfere)
        logging.warning(
            "Skip destroying snapshot %s: external holds present (%s) or holding disabled.",
            quote(snapshot), ", ".join(quote(hold) for hold in holds) if holds else "none",
        )

    # Make snapshots unique
    snapshots_to_destroy = list(set(snapshots_to_destroy))

    # If no snapshots to release, we're done
    if not snapshots_to_release_by_hold and not snapshots_to_destroy:
        if dry_run:
            logging.info("No snapshots to release or destroy in dry-run mode.")
        else:
            logging.info("No snapshots to release or destroy.")
        return

    # Release holds (optional)
    if hold_snapshots or force_release:
        if force_release and (
                len(snapshots_to_release_by_hold.keys()) > 1 or (
                snapshots_to_release_by_hold and hold_name not in snapshots_to_release_by_hold)):
            # Warn if force release is about to release external holds
            logging.warning("Force release is enabled, releasing %d hold%s on %d snapshot%s: %s",
                            len(snapshots_to_release_by_hold.keys()), s(len(snapshots_to_release_by_hold.keys())),
                            sum([len(snapshots) for snapshots in snapshots_to_release_by_hold.values()]),
                            s(sum([len(snapshots) for snapshots in snapshots_to_release_by_hold.values()])),
                            ", ".join(quote(hold) for hold in snapshots_to_release_by_hold.keys()))
        for hold, snapshots_to_release in snapshots_to_release_by_hold.items():
            zfs_release_snapshots(
                snapshots_to_release,
                hold,
                recursive=recursive,
                dry_run=dry_run,
            )

    # If no snapshots to destroy, we're done
    if not snapshots_to_destroy:
        if dry_run:
            logging.info("No snapshots to destroy in dry-run mode.")
        else:
            logging.info("No snapshots to destroy.")
        return

    # Destroy snapshots
    zfs_destroy_snapshots(
        snapshots_to_destroy,
        recursive=recursive,
        dry_run=dry_run,
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
    dataset_plans: List[DatasetPlan] = []

    for root_dataset in root_datasets:
        mountpoint_by_dataset = get_mountpoints_recursively(root_dataset)
        # dataset -> include mode
        include_modes: Dict[str, str] = {}
        for dataset in mountpoint_by_dataset.keys():
            include_mode = zfs_get([dataset], [property_include]).get(dataset, {}).get(property_include,
                                                                                       "").strip().lower()
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
                dataset_plans.append(DatasetPlan(
                    dataset=dataset,
                    mountpoint=mountpoint,
                    include_mode=include_mode,
                    recursive_for_snapshot=recursive_flag,
                    process_self=process_self,
                ))

    return dataset_plans


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
    for dataset_plan in dataset_plans:
        snapshots = list_snapshots_for_dataset(dataset_plan.dataset, snapshot_prefix)
        for snapshot in snapshots:
            snapshot_name = snapshot.split("@", 1)[1]
            properties = zfs_get([snapshot], [property_snapshot_timestamp]).get(snapshot, {})
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
) -> Dict[str, List[str]]:
    """
    Return a dictionary of orphaned snapshots that match our prefix but do not belong to the current run timestamp.
    The keys are snapshot names, and the values are lists of datasets that have these snapshots.
    """
    orphan_datasets_by_snapshot_name: Dict[str, List[str]] = {}
    for dataset_plan in dataset_plans:
        snapshots: List[str] = list_snapshots_for_dataset(dataset_plan.dataset, snapshot_prefix)
        for snapshot in snapshots:
            dataset, snapshot_name = snapshot.split("@", 1)
            properties = zfs_get([snapshot], [property_snapshot_timestamp]).get(snapshot, {})
            timestamp = properties.get(property_snapshot_timestamp, "").strip()
            if not timestamp.isdigit():
                if snapshot_name.startswith(snapshot_prefix):
                    suffix = snapshot_name[len(snapshot_prefix):]
                    timestamp = suffix if suffix.isdigit() else ""
            if timestamp != timestamp_current:
                if snapshot_name not in orphan_datasets_by_snapshot_name:
                    orphan_datasets_by_snapshot_name[snapshot_name] = []
                orphan_datasets_by_snapshot_name[snapshot_name].append(dataset)
    return orphan_datasets_by_snapshot_name


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


def pbs_status(
        *,
        repository: str,
        secret: Optional[str],  # password/token
        dry_run: bool = True,
) -> None:
    """
    Check if the Proxmox Backup Server repository is accessible.
    """
    env = {}
    if repository:
        env["PBS_REPOSITORY"] = repository
    else:
        logging.error("PBS repository must be specified.")
        sys.exit(1)
    if secret:
        env["PBS_PASSWORD"] = secret
    else:
        logging.error("PBS secret (password or token) must be specified.")
        sys.exit(1)

    cmd: List[str] = ["proxmox-backup-client", "status"]
    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message="Check PBS repository status",
        dry_run=dry_run,
        read_only=True,
        env=env,
        debug_log=True,
        check=False
    )
    logging.debug("PBS repository status:%s",
                  "\n" + completed_process.stdout.decode().strip() if completed_process.stdout else " No output")
    if completed_process.returncode != 0:
        error_message: str = completed_process.stderr.decode().strip() if completed_process.stderr else None
        if error_message.lower() == "error: permission check failed":
            logging.error(
                "PBS repository %s is not accessible: permission check failed. "
                "Check your username, token, and repository settings.",
                quote(repository)
            )
        elif error_message.lower() == "error: unable to get (default) repository":
            logging.error("PBS repository string not properly passed to the command. ")
        else:
            logging.error("PBS repository status check failed %s:\n%s", quote(repository), error_message)
        sys.exit(1)


def pbs_create_archive_name(*,
                            dataset: str,
                            mountpoint: str,
                            snapshot_name: str,
                            archive_name_prefix: Optional[str]
                            ) -> str:
    """
    Create a Proxmox Backup Server archive name for the dataset snapshot.

    Format: <archive_name_prefix><dataset with '/' -> '_'>.pxar:<snapshot directory>
    """
    snapshot_directory = snapshot_path_on_disk(mountpoint, snapshot_name)
    if not snapshot_directory.exists():
        logging.error("Skip dataset %s: snapshot directory %s does not exist.",
                      quote(dataset), quote(str(snapshot_directory)))
        sys.exit(1)
    elif not snapshot_directory.is_dir():
        logging.warning("Skip dataset %s: snapshot directory %s is not a directory.",
                        quote(dataset), quote(str(snapshot_directory)))
        # sys.exit(1) # Even if it's not a directory, we could still archive it
    elif not os.access(snapshot_directory, os.R_OK):
        logging.error("Skip dataset %s: snapshot directory %s is not readable.",
                      quote(dataset), quote(str(snapshot_directory)))
        # sys.exit(1)

    dataset_id = dataset.replace("/", "_")
    return f"{archive_name_prefix or ""}{dataset_id}.pxar:{str(snapshot_directory)}"


def pbs_backup_dataset_snapshot(
        *,
        dataset_plans: List[DatasetPlan],
        snapshot_name: str,
        repository: str,
        secret: Optional[str],  # password/token secret; if None, we will prompt only on executing
        namespace: Optional[str],
        backup_id: str,
        backup_time: str,
        archive_name_prefix: Optional[str],
        encryption_password: Optional[str],
        fingerprint: Optional[str],
        pbs_change_detection_mode: Optional[str],
        dry_run: bool,
) -> None:
    """
    Back up the snapshot directory as a pxar archive using proxmox-backup-client.
    """
    archive_names = [
        pbs_create_archive_name(
            dataset=dataset_plan.dataset,
            mountpoint=dataset_plan.mountpoint,
            snapshot_name=snapshot_name,
            archive_name_prefix=archive_name_prefix,
        )
        for dataset_plan in dataset_plans
    ]

    env = {}
    if repository:
        env["PBS_REPOSITORY"] = repository
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

    cmd: List[str] = ["proxmox-backup-client", "backup"]
    cmd.extend(archive_names)
    cmd += ["--backup-type", "host"]
    cmd += ["--backup-id", backup_id]
    cmd += ["--backup-time", backup_time]

    if namespace:
        cmd += ["--ns", namespace]

    if pbs_change_detection_mode:
        if pbs_change_detection_mode not in {"legacy", "data", "metadata"}:
            logging.error("Invalid PBS change detection mode: %s", quote(pbs_change_detection_mode))
            sys.exit(1)
        cmd += ["--change-detection-mode", pbs_change_detection_mode]

    if dry_run:
        cmd.append("--dry-run")

    completed_process: subprocess.CompletedProcess = run_cmd(
        cmd,
        message=f"Back up {len(archive_names)} snapshot{s(archive_names)} {quote(snapshot_name)} to PBS repository {quote(repository)} as backup-id {quote(backup_id)} in namespace {quote(namespace)} with timestamp {quote(backup_time)}",
        dry_run=dry_run,
        read_only=False,
        env=env,
        check=False
    )
    logging.debug("PBS backup:%s",
                  "\n" + completed_process.stdout.decode().strip() if completed_process.stdout else " No output")
    if completed_process.returncode != 0:
        # If the command failed, it's either because the dataset does not exist or we don't have enough permissions.
        logging.error(
            "Failed to back up snapshot %s to PBS repository %s:\n%s", quote(snapshot_name), quote(repository),
            completed_process.stderr.decode().strip() if completed_process.stderr else "Unknown error"
        )
        raise subprocess.CalledProcessError(
            completed_process.returncode,
            cmd,
            output=completed_process.stdout,
            stderr=completed_process.stderr
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


def create_and_hold_snapshots(
        dataset_plans: List[DatasetPlan],
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
    logging.debug("Creating and holding snapshots for %d dataset plans with snapshot name '%s'",
                  len(dataset_plans), snapshot_name)
    # Step 1: gather and minimize recursive roots
    recursive_roots = _minimize_recursive_roots([p.dataset for p in dataset_plans if p.recursive_for_snapshot])

    # Step 2: snapshot recursive roots
    if recursive_roots:
        logging.debug("Creating snapshots for recursive roots: %s", ", ".join(recursive_roots))
        zfs_create_and_hold_snapshots(recursive_roots, snapshot_name, recursive=True, hold_snapshots=hold_snapshots,
                                      hold_name=hold_name, dry_run=dry_run)
    else:
        logging.debug("No recursive roots to snapshot; all datasets are non-recursive.")

    # Step 3: compute a descendants-covered set
    def covered_by_recursive(dataset: str) -> bool:
        return any(
            dataset.startswith(recursive_root + "/") or dataset == recursive_root for recursive_root in recursive_roots)

    non_recursive_candidates = [dataset_plan.dataset for dataset_plan in dataset_plans if
                                not dataset_plan.recursive_for_snapshot]
    non_recursive_targets = [dataset for dataset in non_recursive_candidates if not covered_by_recursive(dataset)]

    # Step 4: snapshot the remaining non-recursive datasets in one go (if any)
    if non_recursive_targets:
        logging.debug("Creating snapshots for non-recursive targets: %s", ", ".join(non_recursive_targets))
        zfs_create_and_hold_snapshots(non_recursive_targets, snapshot_name, recursive=False,
                                      hold_snapshots=hold_snapshots, hold_name=hold_name, dry_run=dry_run)
    else:
        logging.debug("No non-recursive datasets to snapshot; all covered by recursive roots.")


def release_and_destroy_snapshots(
        dataset_plans: List[DatasetPlan],
        *,
        snapshot_name: str,
        hold_snapshots: bool,
        hold_name: str,
        dry_run: bool,
):
    """
    Destroy snapshots efficiently while ensuring each snapshot gets destroyed only once.

    Strategy:
      1) Gather datasets that requested recursive snapshotting and **minimize** them so
         that child datasets under a recursive root are not listed separately.
      2) Destroy root snapshots with -r.
      3) Gather non-recursive datasets and **exclude** any that are descendants of any
         recursive root (they're already covered by step 2).
      4) Destroy the remaining snapshots without -r (can be batched).
    """
    logging.debug("Releasing and destroying snapshots for %d dataset plans with snapshot name '%s'",
                  len(dataset_plans), snapshot_name)
    # Step 1: gather datasets that requested recursive snapshotting and minimize them
    recursive_roots = _minimize_recursive_roots([p.dataset for p in dataset_plans if p.recursive_for_snapshot])

    # Step 2: destroy recursive roots
    if recursive_roots:
        logging.debug("Releasing and destroying snapshots for recursive roots: %s", ", ".join(recursive_roots))
        zfs_release_and_destroy_snapshots(recursive_roots, snapshot_name, recursive=True, hold_snapshots=hold_snapshots,
                                          hold_name=hold_name, dry_run=dry_run)
    else:
        logging.debug("No recursive roots to release and destroy; all datasets are non-recursive.")

    # Step 3: compute a descendants-covered set
    def covered_by_recursive(dataset: str) -> bool:
        return any(
            dataset.startswith(recursive_root + "/") or dataset == recursive_root for recursive_root in recursive_roots)

    non_recursive_candidates = [dataset_plan.dataset for dataset_plan in dataset_plans if
                                not dataset_plan.recursive_for_snapshot]
    non_recursive_targets = [dataset for dataset in non_recursive_candidates if not covered_by_recursive(dataset)]

    # Step 4: destroy the remaining non-recursive datasets in one go (if any)
    if non_recursive_targets:
        logging.debug("Releasing and destroying snapshots for non-recursive targets: %s",
                      ", ".join(non_recursive_targets))
        zfs_release_and_destroy_snapshots(non_recursive_targets, snapshot_name, recursive=False,
                                          hold_snapshots=hold_snapshots, hold_name=hold_name, dry_run=dry_run)
    else:
        logging.debug("No non-recursive datasets to release and destroy; all covered by recursive roots.")


def mark_snapshot_timestamp(
        dataset_plans: List[DatasetPlan],
        timestamp: str,
        *,
        snapshot_name: str,
        property_snapshot_timestamp: str,
        dry_run: bool,
):
    """
    Stamp the snapshot with the timestamp
    """
    # Generate snapshots
    snapshots: List[str] = [f"{dataset_plan.dataset}@{snapshot_name}" for dataset_plan in dataset_plans]

    # Set the timestamp property
    zfs_set(snapshots, {property_snapshot_timestamp: timestamp}, dry_run=dry_run)


def cleanup_orphans_if_any(
        dataset_plans: List[DatasetPlan],
        *,
        snapshot_prefix: str,
        timestamp_current: str,
        property_snapshot_timestamp: str,
        remove_orphans: str,  # "true" | "false" | "ask" | "only" | "force-release"
        hold_snapshots: bool,
        hold_name: str,
        dry_run: bool,
) -> None:
    """
    Check for orphaned snapshots and remove them if requested.
    """
    # Find orphaned snapshots
    orphan_datasets_by_snapshot_name: Dict[str, List[str]] = find_orphan_snapshots(
        dataset_plans,
        snapshot_prefix=snapshot_prefix,
        timestamp_current=timestamp_current,
        property_snapshot_timestamp=property_snapshot_timestamp
    )
    if not orphan_datasets_by_snapshot_name:
        return

    orphan_snapshot_length: int = sum([len(dataset) for dataset in orphan_datasets_by_snapshot_name.keys()])

    if remove_orphans == "false":
        logging.warning(
            "Found %d orphaned snapshot%s with prefix %s; not removing (--remove-orphans=false).",
            orphan_snapshot_length, s(orphan_snapshot_length), quote(snapshot_prefix),
        )
        return

    if remove_orphans == "ask":
        logging.warning(
            "Found %d orphaned snapshot%s with prefix %s. There might be another instance using them.",
            orphan_snapshot_length, s(orphan_snapshot_length), quote(snapshot_prefix),
        )
        answer = input("Remove orphaned snapshots now? [y/N]: ").strip().lower()
        if answer != "y":
            logging.info("Skipping orphan removal.")
            return

    if remove_orphans not in ["true", "ask", "only", "force-release"]:
        logging.error(
            "Invalid --remove-orphans value: %s; expected 'true', 'false', 'ask', 'only', or 'force-release'.",
            quote(remove_orphans)
        )
        sys.exit(1)
    logging.info("Removing %d orphaned snapshot%s with prefix %s.",
                 orphan_snapshot_length, s(orphan_snapshot_length), quote(snapshot_prefix))

    # Release holds and destroy orphaned snapshots
    for snapshot_name, datasets in orphan_datasets_by_snapshot_name.items():
        zfs_release_and_destroy_snapshots(
            datasets,
            snapshot_name,
            recursive=False,
            hold_snapshots=hold_snapshots,
            hold_name=hold_name,
            dry_run=dry_run,
            force_release=remove_orphans == "force-release",
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
    # Change detection mode
    g_pbs_b.add_argument("--pbs-change-detection-mode", choices=["legacy", "data", "metadata"], default="metadata",
                         help="Proxmox’s default file-based backups read all data into a pxar archive and check chunks for deduplication, which is slow if most files are unchanged. Switching to metadata-based change detection avoids re-reading files with unchanged metadata by splitting backups into two files (mpxar for metadata and ppxar for contents) for faster lookups. Data mode also creates split archives but re-encodes all file data without using previous metadata.")

    # ZFS options (group)
    g_zfs = p.add_argument_group("ZFS options")
    g_zfs.add_argument("-H", "--hold-snapshots", action=argparse.BooleanOptionalAction, default=True,
                       help="Hold temporary snapshots until they are backed up.")
    g_zfs.add_argument("-X", "--exclude-empty-parents", action=argparse.BooleanOptionalAction, default=True,
                       help="If a dataset has children and is empty itself, skip backing up the parent dataset.")
    g_zfs.add_argument("-O", "--remove-orphans", choices=["true", "false", "ask", "only", "force-release"],
                       default="ask",
                       help="Remove orphaned snapshots whose timestamp does not match the current run. Option 'only' will only remove orphans and not create new snapshots or backups. Option 'force-release' will release all (even external) holds on orphaned snapshots and destroy them.")

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

    logging.basicConfig(level=level, format="[%(asctime)s][%(levelname)7s]: %(message)s")


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
    dataset_plans = collect_datasets_to_backup(
        root_datasets=args.datasets,
        property_include=args.zfs_include_property,
        exclude_empty_parents=args.exclude_empty_parents,
    )
    if not dataset_plans:
        logging.warning("No datasets selected. Check %s property values.", quote(args.zfs_include_property))
        return 0

    # Determine snapshot name
    timestamp_now = str(int(time.time()))
    if args.resume:
        timestamp_newest = find_resume_timestamp(dataset_plans, snapshot_prefix=args.zfs_snapshot_prefix,
                                                 property_snapshot_timestamp=args.zfs_snapshot_timestamp_property)
        if not timestamp_newest:
            logging.warning("Resume requested, but no suitable existing timestamp found. Aborting.")
            return 1
        snapshot_name = f"{args.zfs_snapshot_prefix}{timestamp_newest}"
        timestamp_current = timestamp_newest
        logging.warning("Resuming: skipping snapshot creation and using existing timestamp %s (snapshot %s).",
                        timestamp_newest, quote(snapshot_name))
        if not dataset_plans:
            logging.info("Nothing to do after resume filtering.")
            return 0
    else:
        snapshot_name = f"{args.zfs_snapshot_prefix}{timestamp_now}"
        timestamp_current = timestamp_now

    # Log the snapshot name and timestamp
    logging.debug("Snapshot name: %s, timestamp current: %s, timestamp now: %s", quote(snapshot_name),
                  timestamp_current, timestamp_now)

    # Orphan cleanup (ask/true/false/only/force-release)
    cleanup_orphans_if_any(
        dataset_plans,
        snapshot_prefix=args.zfs_snapshot_prefix,
        timestamp_current=timestamp_current,
        property_snapshot_timestamp=args.zfs_snapshot_timestamp_property,
        remove_orphans=args.remove_orphans,
        hold_snapshots=args.hold_snapshots,
        hold_name=args.zfs_hold_name,
        dry_run=not args.execute,
    )

    # If we are only removing orphans, we can exit early
    if args.remove_orphans == "only":
        logging.info("Orphan cleanup completed. Exiting as per --remove-orphans=only.")
        return 0

    # Create snapshots (unless resuming)
    if not args.resume:
        create_and_hold_snapshots(
            dataset_plans,
            snapshot_name=snapshot_name,
            hold_snapshots=args.hold_snapshots,
            hold_name=args.zfs_hold_name,
            dry_run=not args.execute,
        )
        # Stamp timestamp
        mark_snapshot_timestamp(
            dataset_plans,
            timestamp_current,
            snapshot_name=snapshot_name,
            property_snapshot_timestamp=args.zfs_snapshot_timestamp_property,
            dry_run=not args.execute,
        )
    else:
        logging.info("Snapshot creation skipped due to resume mode.")

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

    # Check PBS repository status
    pbs_status(
        repository=pbs_repository,
        secret=pbs_secret if pbs_secret else None,
        dry_run=not args.execute,
    )

    # Backup the snapshots to Proxmox Backup Server
    pbs_backup_dataset_snapshot(
        dataset_plans=dataset_plans,
        snapshot_name=snapshot_name,
        repository=pbs_repository,
        secret=pbs_secret if pbs_secret else None,
        namespace=args.pbs_namespace if args.pbs_namespace else None,
        backup_id=args.pbs_backup_id if args.pbs_backup_id else socket.gethostname(),
        backup_time=timestamp_current,
        archive_name_prefix=args.pbs_archive_name_prefix if args.pbs_archive_name_prefix else None,
        encryption_password=args.pbs_encryption_password if args.pbs_encryption_password else None,
        fingerprint=args.pbs_fingerprint if args.pbs_fingerprint else None,
        pbs_change_detection_mode=args.pbs_change_detection_mode,
        dry_run=not args.execute,
    )

    # Tear-down: release holds (if ours) and destroy snapshots
    release_and_destroy_snapshots(
        dataset_plans,
        snapshot_name=snapshot_name,
        hold_snapshots=args.hold_snapshots,
        hold_name=args.zfs_hold_name,
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
