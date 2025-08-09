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
2) Optionally remove orphaned snapshots (ask/true/false).
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
import json
import logging
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# =============================================================================
# Defaults & constants
# =============================================================================

DEFAULT_INCLUDE_PROP = "zfs-pbs-backup:include"  # "true" | "false" | "recursive" | "children"
DEFAULT_SNAP_TS_PROP = "zfs-pbs-backup:unix_timestamp"  # snapshot property storing unix timestamp
DEFAULT_SNAP_DONE_PROP = "zfs-pbs-backup:backed_up"  # snapshot property: "true" after successful backup
DEFAULT_SNAP_PREFIX = "zfs-pbs-backup_"
DEFAULT_HOLD_NAME = "zfs-pbs-backup"

READ_ONLY_ZFS_SUBCMDS = {
    ("zfs", "list"),
    ("zfs", "get"),
    ("zfs", "holds"),
}


# =============================================================================
# Small utilities
# =============================================================================

def quote(s: str) -> str:
    """Return s wrapped in double quotes for logging."""
    return f'"{s}"'


def infer_read_only(cmd: List[str]) -> bool:
    """
    Heuristic to decide if a command is read-only. Used to allow running discovery
    commands even in dry-run mode, while blocking mutating ones.
    """
    if not cmd:
        return True
    head = tuple(cmd[:2]) if len(cmd) >= 2 else (cmd[0], "")
    if head in READ_ONLY_ZFS_SUBCMDS:
        return True
    if cmd[:1] == ["proxmox-backup-client"] and "backup" not in cmd:
        return True
    return False


def which(prog: str) -> Optional[str]:
    """Return absolute path to prog if found in PATH, else None."""
    from shutil import which as _which
    return _which(prog)


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
) -> subprocess.CompletedProcess:
    """
    Run a command with structured logging and timing.

    - If `message` is provided, log it (prefix with "[dry-run]" if applicable).
    - Ensure the command itself is debug-logged at most once per call, and at least
      once if it's not read-only.
    - In dry-run mode, **read-only commands still execute** (for discovery); mutating
      commands return a dummy success result without execution.
    - Always debug-log the elapsed time.

    Returns a subprocess.CompletedProcess (or a synthetic one in dry-run for mutating).
    """
    ro = infer_read_only(cmd) if read_only is None else read_only

    if message:
        if dry_run and not ro:
            logging.info("[dry-run] %s", message)
        else:
            logging.info("%s", message)

    cmd_str = " ".join(shlex.quote(c) for c in cmd)
    if ro:
        logging.debug("cmd: %s", cmd_str)
    else:
        logging.debug("cmd (mutating): %s", cmd_str)

    if dry_run and not ro:
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    start = time.perf_counter()
    try:
        cp = subprocess.run(
            cmd,
            env={**os.environ, **(env or {})},
            check=check,
            capture_output=capture_output,
        )
        return cp
    finally:
        elapsed = time.perf_counter() - start
        logging.debug("time: %.3fs for: %s", elapsed, cmd_str)


# =============================================================================
# ZFS helpers (the only place that runs zfs commands)
# =============================================================================

def zfs_list(
        what: str,
        *,
        dataset: Optional[str] = None,
        recursive: bool = False,
        columns: List[str] = ["name"],
        types: List[str] = ["filesystem"],
) -> List[List[str]]:
    """
    List ZFS objects with parsable output.

    Equivalent to:
        zfs list -H -p -o <cols> [-r] -t <types> [dataset]

    Returns a list of rows; each row is a list of column strings.
    """
    cmd = ["zfs", "list", "-H", "-p", "-o", ",".join(columns)]
    if recursive:
        cmd.append("-r")
    if types:
        cmd += ["-t", ",".join(types)]
    if dataset:
        cmd.append(dataset)
    cp = run_cmd(cmd, message=None, dry_run=False, read_only=True)
    out = cp.stdout.decode().splitlines()
    return [line.split("\t") for line in out if line.strip()]


def zfs_get(
        props: List[str],
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
        ",".join(props),
        target,
    ]
    cp = run_cmd(cmd, dry_run=False, read_only=True)
    result: Dict[str, str] = {}
    for line in cp.stdout.decode().splitlines():
        if not line.strip():
            continue
        prop, value, _source = line.split("\t", 2)
        result[prop] = value
    return result


def zfs_set(prop: str, value: str, target: str, *, dry_run: bool):
    """Set a single ZFS property."""
    cmd = ["zfs", "set", f"{prop}={value}", target]
    run_cmd(
        cmd,
        message=f"Set ZFS property {quote(prop)}={quote(value)} on {quote(target)}",
        dry_run=dry_run,
        read_only=False,
    )


def zfs_snapshot_create(
        targets: List[str],
        snapname: str,
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
    cmd += [f"{t}@{snapname}" for t in targets]
    run_cmd(cmd, message=f"Create snapshot(s) {quote(snapname)}", dry_run=dry_run, read_only=False)

    # Hold snapshots (optional)
    if hold:
        cmd = ["zfs", "hold"]
        if recursive:
            cmd.append("-r")
        cmd += [hold_name] + [f"{t}@{snapname}" for t in targets]
        run_cmd(
            cmd,
            message=f"Hold snapshot(s) {quote(snapname)} with hold name {quote(hold_name)}",
            dry_run=dry_run,
            read_only=False,
        )


def zfs_holds(target_snap: str) -> List[str]:
    """
    List holds on a snapshot.

    Equivalent to:
        zfs holds -H <snapshot>

    Returns a list of hold tags.
    """
    cmd = ["zfs", "holds", "-H", target_snap]
    try:
        cp = run_cmd(cmd, dry_run=False, read_only=True)
        holds = []
        for line in cp.stdout.decode().splitlines():
            if not line.strip():
                continue
            # <snapshot>\t<tag>\t<timestamp>
            _snap, tag, _ts = line.split("\t", 2)
            holds.append(tag)
        return holds
    except subprocess.CalledProcessError:
        # Snapshot may not exist
        return []


def zfs_release_hold(target_snap: str, hold_name: str, *, dry_run: bool):
    """Release a hold on a snapshot."""
    cmd = ["zfs", "release", hold_name, target_snap]
    run_cmd(
        cmd,
        message=f"Release hold {quote(hold_name)} on {quote(target_snap)}",
        dry_run=dry_run,
        read_only=False,
    )


def zfs_destroy_snapshot(target_snap: str, *, dry_run: bool):
    """Destroy a snapshot."""
    cmd = ["zfs", "destroy", target_snap]
    run_cmd(
        cmd,
        message=f"Destroy snapshot {quote(target_snap)}",
        dry_run=dry_run,
        read_only=False,
    )


def get_mountpoints_recursively(root_dataset: str) -> Dict[str, str]:
    """
    Return {dataset: mountpoint} for root and all descendant filesystems.
    """
    rows = zfs_list(
        "filesystems",
        dataset=root_dataset,
        recursive=True,
        columns=["name", "mountpoint"],
        types=["filesystem"],
    )
    return {name: mnt for name, mnt in rows}


def list_snapshots_for_dataset(dataset: str, prefix: str) -> List[str]:
    """
    Return full snapshot names for this dataset that start with the given prefix.
    e.g. "pool/data@zfs-pbs-backup_1699999999"
    """
    rows = zfs_list(
        "snapshots",
        dataset=dataset,
        recursive=False,
        columns=["name"],
        types=["snapshot"],
    )
    snaps = [r[0] for r in rows if r]
    full_prefix = f"{dataset}@{prefix}"
    return [s for s in snaps if s.startswith(full_prefix)]


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
        snapname: str,
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
    full = f"{dataset}@{snapname}"
    holds = zfs_holds(full)
    if not holds:
        zfs_destroy_snapshot(full, dry_run=dry_run)
        return

    if holding_enabled and set(holds) == {our_hold_name}:
        zfs_release_hold(full, our_hold_name, dry_run=dry_run)
        zfs_destroy_snapshot(full, dry_run=dry_run)
        return

    logging.warning(
        "Skip destroying snapshot %s: external holds present (%s) or holding disabled.",
        quote(full), ", ".join(quote(h) for h in holds) if holds else "none",
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
    Only checks immediate entries (shallow scan).
    """
    parent = Path(parent_mnt)
    try:
        entries = list(parent.iterdir())
    except Exception:
        # Permission or transient issue: treat as not empty (conservative)
        return False

    child_mounts_set = {os.path.abspath(p) for p in child_mounts}
    for e in entries:
        abs_e = os.path.abspath(str(e))
        if abs_e in child_mounts_set:
            continue
        # Any other file/dir => not empty
        return False
    return True


def collect_datasets_to_backup(
        roots: List[str],
        *,
        include_prop: str,
        exclude_empty_parents: bool,
) -> List[DatasetPlan]:
    """
    Inspect include modes (true/false/recursive/children) and construct a plan.

    - "recursive" and "children" trigger -r snapshotting.
    - "children" excludes processing the parent itself (children only).
    - Optionally skip empty parents (with children) for "true"/"recursive".
    """
    plans: List[DatasetPlan] = []

    for root in roots:
        mnts = get_mountpoints_recursively(root)
        # dataset -> include mode
        modes: Dict[str, str] = {}
        for ds in mnts.keys():
            mode = zfs_get([include_prop], ds).get(include_prop, "").strip().lower()
            if mode == "":
                mode = "false"
            if mode not in {"true", "false", "recursive", "children"}:
                logging.warning(
                    "Dataset %s has unknown %s=%s; treating as false.",
                    quote(ds), quote(include_prop), quote(mode)
                )
                mode = "false"
            modes[ds] = mode

        # Precompute child mountpoints for empty-parent checks
        children_by_parent: Dict[str, List[str]] = {ds: [] for ds in mnts.keys()}
        for ds in mnts.keys():
            for other in mnts.keys():
                if other != ds and other.startswith(ds + "/"):
                    children_by_parent[ds].append(mnts[other])

        for ds, mnt in mnts.items():
            mode = modes[ds]
            recursive_flag = mode in {"recursive", "children"}
            process_self = mode in {"true", "recursive"}

            if process_self and exclude_empty_parents:
                child_mounts = children_by_parent.get(ds, [])
                if child_mounts and is_parent_empty_excluding_child_mounts(mnt, child_mounts):
                    process_self = False
                    logging.info("Skip empty parent dataset %s at %s", quote(ds), quote(mnt))

            if mode != "false":
                plans.append(DatasetPlan(
                    dataset=ds,
                    mountpoint=mnt,
                    include_mode=mode,
                    recursive_for_snapshot=recursive_flag,
                    process_self=process_self,
                ))

    return plans


# =============================================================================
# Resume & orphan discovery
# =============================================================================

def find_resume_timestamp(
        datasets: List[DatasetPlan],
        *,
        snap_prefix: str,
        snap_ts_prop: str,
) -> Optional[str]:
    """
    Find the newest unix timestamp among snapshots that match the prefix.
    Prefer the stored property; fall back to parsing the name suffix.
    """
    newest_ts: Optional[str] = None
    for plan in datasets:
        snaps = list_snapshots_for_dataset(plan.dataset, snap_prefix)
        for fullsnap in snaps:
            snapname = fullsnap.split("@", 1)[1]
            props = zfs_get([snap_ts_prop], fullsnap)
            ts = props.get(snap_ts_prop, "").strip()
            if ts.isdigit():
                if newest_ts is None or int(ts) > int(newest_ts):
                    newest_ts = ts
            else:
                if snapname.startswith(snap_prefix):
                    suffix = snapname[len(snap_prefix):]
                    if suffix.isdigit() and (newest_ts is None or int(suffix) > int(newest_ts)):
                        newest_ts = suffix
    return newest_ts


def find_orphan_snapshots(
        datasets: List[DatasetPlan],
        *,
        snap_prefix: str,
        current_ts: str,
        snap_ts_prop: str,
) -> List[Tuple[str, str]]:
    """
    Return a list of (dataset, snapname) for snapshots that match our prefix but
    do not belong to the current run timestamp.
    """
    orphans: List[Tuple[str, str]] = []
    for plan in datasets:
        snaps = list_snapshots_for_dataset(plan.dataset, snap_prefix)
        for fullsnap in snaps:
            ds, snapname = fullsnap.split("@", 1)
            props = zfs_get([snap_ts_prop], fullsnap)
            ts = props.get(snap_ts_prop, "").strip()
            if not ts.isdigit():
                if snapname.startswith(snap_prefix):
                    suffix = snapname[len(snap_prefix):]
                    ts = suffix if suffix.isdigit() else ""
            if ts != current_ts:
                orphans.append((ds, snapname))
    return orphans


# =============================================================================
# Proxmox Backup Server helper
# =============================================================================

def pbs_backup_dataset_snapshot(
        *,
        dataset: str,
        mountpoint: str,
        snapname: str,
        repository: str,
        backup_id_prefix: str,
        namespace: str,
        pbs_username: Optional[str],
        pbs_auth_id: Optional[str],
        pbs_secret: Optional[str],  # password/token secret; if None we will prompt only on execute
        encryption_password: Optional[str],
        dry_run: bool,
) -> None:
    """
    Back up the snapshot directory as a pxar archive using proxmox-backup-client.

    Archive: <dataset with '/' -> '_'>.pxar:<snapshot directory>
    Backup-ID: <backup_id_prefix><dataset>
    """
    snap_dir = snapshot_path_on_disk(mountpoint, snapname)
    if not snap_dir.exists():
        logging.warning("Skip dataset %s: snapshot directory %s does not exist.",
                        quote(dataset), quote(str(snap_dir)))
        return

    ds_id = dataset.replace("/", "_")
    backup_id = f"{backup_id_prefix}{dataset}"
    archive_spec = f"{ds_id}.pxar:{str(snap_dir)}"

    env = {}
    if pbs_secret:
        env["PBS_PASSWORD"] = pbs_secret
    if encryption_password:
        env["PBS_ENCRYPTION_PASSWORD"] = encryption_password

    cmd = [
        "proxmox-backup-client", "backup",
        archive_spec,
        "--repository", repository,
        "--backup-id", backup_id,
    ]
    if namespace:
        cmd += ["--ns", namespace]
    if pbs_auth_id:
        cmd += ["--auth-id", pbs_auth_id]
    elif pbs_username:
        cmd += ["--username", pbs_username]

    run_cmd(
        cmd,
        message=f"Back up dataset {quote(dataset)} snapshot {quote(snapname)} "
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
    roots = sorted(set(recursive_datasets))
    minimized: List[str] = []
    for ds in roots:
        if not any(ds.startswith(parent + "/") for parent in minimized):
            minimized.append(ds)
    return minimized


def create_snapshots_for_plans(
        plans: List[DatasetPlan],
        *,
        snapname: str,
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
        zfs_snapshot_create([root], snapname, recursive=True, hold=hold_snapshots, hold_name=hold_name, dry_run=dry_run)

    # Step 3: compute descendants-covered set
    def covered_by_recursive(ds: str) -> bool:
        return any(ds.startswith(root + "/") or ds == root for root in recursive_roots)

    non_recursive_candidates = [p.dataset for p in plans if not p.recursive_for_snapshot]
    non_recursive_targets = [ds for ds in non_recursive_candidates if not covered_by_recursive(ds)]

    # Step 4: snapshot the remaining non-recursive datasets in one go (if any)
    if non_recursive_targets:
        zfs_snapshot_create(non_recursive_targets, snapname, recursive=False, hold=hold_snapshots, hold_name=hold_name,
                            dry_run=dry_run)


def mark_snapshot_timestamp_and_reset_done(
        plans: List[DatasetPlan],
        *,
        dataset_filter_self_only: bool,
        snapname: str,
        snap_ts_prop: str,
        snap_done_prop: str,
        timestamp: str,
        dry_run: bool,
):
    """
    Stamp the snapshot with the run timestamp and clear the "backed_up" flag.
    Only applies to datasets that we will actually back up when dataset_filter_self_only=True.
    """
    for p in plans:
        if dataset_filter_self_only and not p.process_self:
            continue
        snap_full = f"{p.dataset}@{snapname}"
        zfs_set(snap_ts_prop, timestamp, snap_full, dry_run=dry_run)
        zfs_set(snap_done_prop, "false", snap_full, dry_run=dry_run)


def filter_plans_for_existing_unbacked(
        plans: List[DatasetPlan],
        *,
        snapname: str,
        snap_done_prop: str,
) -> List[DatasetPlan]:
    """
    Keep only datasets where the snapshot exists and is not yet marked as backed up.
    """
    selected: List[DatasetPlan] = []
    for p in plans:
        full = f"{p.dataset}@{snapname}"
        try:
            props = zfs_get([snap_done_prop], full)
        except subprocess.CalledProcessError:
            # Snapshot missing
            continue
        done = props.get(snap_done_prop, "").strip().lower() == "true"
        if not done and p.process_self:
            selected.append(p)
    return selected


def cleanup_orphans_if_any(
        plans: List[DatasetPlan],
        *,
        snap_prefix: str,
        current_ts: str,
        snap_ts_prop: str,
        remove_orphans: str,  # "true" | "false" | "ask"
        holding_enabled: bool,
        hold_name: str,
        dry_run: bool,
) -> None:
    """Find and optionally remove orphan snapshots from previous runs."""
    orphans = find_orphan_snapshots(plans, snap_prefix=snap_prefix, current_ts=current_ts, snap_ts_prop=snap_ts_prop)
    if not orphans:
        return

    if remove_orphans == "false":
        logging.warning(
            "Found %d orphaned snapshot(s) with prefix %s; not removing (--remove-orphans=false).",
            len(orphans), quote(snap_prefix),
        )
        return

    if remove_orphans == "ask":
        logging.warning(
            "Found %d orphaned snapshot(s) with prefix %s. There might be another instance using them.",
            len(orphans), quote(snap_prefix),
        )
        ans = input("Remove orphaned snapshots now? [y/N]: ").strip().lower()
        if ans != "y":
            logging.info("Skipping orphan removal.")
            return

    for ds, snapname in orphans:
        destroy_snapshot_helper(
            ds,
            snapname,
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

    # Repository (common & important) — short + long
    p.add_argument("-R", "--repository", required=True, help="PBS repository (e.g. 'user@pbs@host:datastore').")

    # PBS options (group)
    g_pbs = p.add_argument_group("PBS options")
    g_pbs.add_argument("--pbs-username", help="PBS username for password authentication.")
    g_pbs.add_argument("--pbs-auth-id", help="PBS auth-id for API token authentication (e.g. 'user@pbs!tokenid').")
    g_pbs.add_argument("-P", "--pbs-secret",
                       help="PBS password or API token secret. If omitted, you will be prompted securely when needed.")
    g_pbs.add_argument("-K", "--pbs-encryption-password", default="",
                       help="PBS encryption password (empty disables encryption).")
    g_pbs.add_argument("-N", "--pbs-namespace", default="", help="PBS namespace.")
    g_pbs.add_argument("-B", "--pbs-backup-id-prefix", default="",
                       help="Prefix added to the backup ID (backup ID = prefix + dataset).")

    # ZFS options (group)
    g_zfs = p.add_argument_group("ZFS options")
    g_zfs.add_argument("--zfs-include-property", default=DEFAULT_INCLUDE_PROP,
                       help="ZFS dataset property controlling include mode: true/false/recursive/children.")
    g_zfs.add_argument("--zfs-snapshot-timestamp-property", default=DEFAULT_SNAP_TS_PROP,
                       help="ZFS snapshot property storing the unix timestamp for this run.")
    g_zfs.add_argument("--zfs-snapshot-done-property", default=DEFAULT_SNAP_DONE_PROP,
                       help="ZFS snapshot property set to 'true' after a successful backup.")
    g_zfs.add_argument("--zfs-snapshot-prefix", default=DEFAULT_SNAP_PREFIX,
                       help="Prefix for snapshot names (final name is <prefix><timestamp>).")
    g_zfs.add_argument("--zfs-hold-name", default=DEFAULT_HOLD_NAME,
                       help="Hold name to apply to temporary snapshots.")

    g_zfs.add_argument("-H", "--hold-snapshots", action=argparse.BooleanOptionalAction, default=True,
                       help="Hold temporary snapshots until they are backed up.")
    g_zfs.add_argument("-X", "--exclude-empty-parents", action=argparse.BooleanOptionalAction, default=True,
                       help="If a dataset has children and is empty itself, skip backing up the parent dataset.")
    g_zfs.add_argument("-O", "--remove-orphans", choices=["true", "false", "ask"], default="ask",
                       help="Remove orphaned snapshots whose timestamp does not match the current run.")

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
    missing = [prog for prog in ("zfs", "proxmox-backup-client") if which(prog) is None]
    if missing:
        logging.error("Missing required tools: %s", ", ".join(missing))
        sys.exit(2)


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
    ensure_tools()

    # Prompt for secret only when actually needed (execute or resume)
    pbs_secret = args.pbs_secret
    if (args.execute or args.resume) and not pbs_secret:
        try:
            pbs_secret = secure_prompt("PBS password or API token secret (leave empty to skip): ")
        except (EOFError, KeyboardInterrupt):
            pbs_secret = ""

    # Build the plan
    plans = collect_datasets_to_backup(
        roots=args.datasets,
        include_prop=args.zfs_include_property,
        exclude_empty_parents=args.exclude_empty_parents,
    )
    if not plans:
        logging.warning("No datasets selected. Check %s property values.", quote(args.zfs_include_property))
        return 0

    # Determine snapshot name
    now_ts = str(int(time.time()))
    if args.resume:
        newest = find_resume_timestamp(plans, snap_prefix=args.zfs_snapshot_prefix,
                                       snap_ts_prop=args.zfs_snapshot_timestamp_property)
        if not newest:
            logging.warning("Resume requested, but no suitable existing timestamp found. Aborting.")
            return 1
        snapname = f"{args.zfs_snapshot_prefix}{newest}"
        logging.warning("Resuming: skipping snapshot creation and using existing timestamp %s (snapshot %s).",
                        newest, quote(snapname))
        # Only process datasets that have this snapshot and are not marked done
        plans = filter_plans_for_existing_unbacked(plans, snapname=snapname,
                                                   snap_done_prop=args.zfs_snapshot_done_property)
        if not plans:
            logging.info("Nothing to do after resume filtering.")
            return 0
    else:
        snapname = f"{args.zfs_snapshot_prefix}{now_ts}"

    # Orphan cleanup (ask/true/false)
    cleanup_orphans_if_any(
        plans,
        snap_prefix=args.zfs_snapshot_prefix,
        current_ts=snapname[len(args.zfs_snapshot_prefix):],
        snap_ts_prop=args.zfs_snapshot_timestamp_property,
        remove_orphans=args.remove_orphans,
        holding_enabled=args.hold_snapshots,
        hold_name=args.zfs_hold_name,
        dry_run=not args.execute,
    )

    # Create snapshots (unless resuming)
    if not args.resume:
        create_snapshots_for_plans(
            plans,
            snapname=snapname,
            hold_snapshots=args.hold_snapshots,
            hold_name=args.zfs_hold_name,
            dry_run=not args.execute,
        )
        # Stamp timestamp and clear done flag on snapshots we will actually back up
        mark_snapshot_timestamp_and_reset_done(
            plans,
            dataset_filter_self_only=True,
            snapname=snapname,
            snap_ts_prop=args.zfs_snapshot_timestamp_property,
            snap_done_prop=args.zfs_snapshot_done_property,
            timestamp=snapname[len(args.zfs_snapshot_prefix):],
            dry_run=not args.execute,
        )
    else:
        logging.info("Snapshot creation skipped due to resume mode.")

    # Filter to actionable items (existing snapshot & not done)
    if not args.resume:
        plans = filter_plans_for_existing_unbacked(plans, snapname=snapname,
                                                   snap_done_prop=args.zfs_snapshot_done_property)
        if not plans:
            logging.info("Nothing to back up (already done?).")
            return 0

    # Backup loop
    for p in plans:
        pbs_backup_dataset_snapshot(
            dataset=p.dataset,
            mountpoint=p.mountpoint,
            snapname=snapname,
            repository=args.repository,
            backup_id_prefix=args.pbs_backup_id_prefix,
            namespace=args.pbs_namespace,
            pbs_username=args.pbs_username,
            pbs_auth_id=args.pbs_auth_id,
            pbs_secret=pbs_secret if pbs_secret else None,
            encryption_password=args.pbs_encryption_password if args.pbs_encryption_password else None,
            dry_run=not args.execute,
        )
        # Mark as backed up
        full = f"{p.dataset}@{snapname}"
        zfs_set(args.zfs_snapshot_done_property, "true", full, dry_run=not args.execute)

    # Tear-down: release holds (if ours) and destroy snapshots
    for p in plans:
        destroy_snapshot_helper(
            p.dataset,
            snapname,
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
