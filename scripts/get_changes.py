#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# TODO How do we handle different containers in the same stack that all gets the same update (like Radarr and Sonarr)?
# Should it create multiple ZFS Snapshots that contain identical data?


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments with environment variable defaults."""
    parser = argparse.ArgumentParser(
        description="Analyze git commits and extract Docker image changes from compose files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage (uses current directory, outputs to stdout)
  ./get_changes.py

  # Save results to a file
  ./get_changes.py --output results.json

  # Specify a custom repository
  ./get_changes.py --repo-dir /path/to/repo

  # Check a specific branch
  ./get_changes.py --branch develop --remote upstream

  # Skip fetching updates
  ./get_changes.py --skip-fetch

  # Enable verbose output for debugging
  ./get_changes.py --verbose

  # Combine options
  ./get_changes.py --repo-dir /path/to/repo --branch develop --output changes.json --verbose

Environment Variables (used as defaults):
  REPO_DIR     - Repository directory (default: current working directory)
  REMOTE       - Git remote name (default: origin)
  BRANCH       - Branch name (default: main)
  TAG          - Last tag/commit to start from (default: update/last)
  SKIP_FETCH   - Skip fetching from remote (default: false)

Command-line arguments take precedence over environment variables.
        """,
    )

    parser.add_argument(
        "--repo-dir",
        type=str,
        default=os.environ.get("REPO_DIR", os.getcwd()),
        help="Path to the git repository (default: current working directory)",
        metavar="PATH",
    )

    parser.add_argument(
        "--remote",
        type=str,
        default=os.environ.get("REMOTE", "origin"),
        help="Git remote name (default: origin)",
        metavar="NAME",
    )

    parser.add_argument(
        "--branch",
        type=str,
        default=os.environ.get("BRANCH", "main"),
        help="Git branch name to track (default: main)",
        metavar="NAME",
    )

    parser.add_argument(
        "--tag",
        type=str,
        default=os.environ.get("TAG", "update/last"),
        help="Last tag/commit reference to start from (default: update/last)",
        metavar="TAG",
    )

    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        default=os.environ.get("SKIP_FETCH", "false").lower() == "true",
        help="Skip fetching from remote before processing",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output for debugging",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file for results (default: stdout)",
        metavar="OUTPUT",
    )

    return parser.parse_args()


def verbose_print(message: str, verbose: bool = False) -> None:
    """Print a message to stderr if verbose mode is enabled.

    Args:
        message: Message to print
        verbose: If True, print the message
    """
    if verbose:
        print(f"[VERBOSE] {message}", file=sys.stderr)


def run_git_command(cmd: List[str], check: bool = True, repo_dir: str = None, verbose: bool = False) -> str:
    """Execute a git command and return stdout.

    Args:
        cmd: Git command to run (without 'git' prefix, will be added automatically)
        check: If True, raise exception on non-zero exit code
        repo_dir: Working directory for git command (uses cwd if not specified)
        verbose: If True, print verbose output

    Returns:
        Stripped stdout from the command
    """
    if repo_dir is None:
        repo_dir = os.getcwd()

    verbose_print(f"Running git command: {' '.join(cmd)}", verbose)

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )
        if verbose and result.stdout:
            verbose_print(f"Git output ({len(result.stdout)} chars): {result.stdout[:200]}", verbose)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            print(f"Git command failed: {' '.join(cmd)}", file=sys.stderr)
            print(f"Error: {e.stderr}", file=sys.stderr)
            raise
        return ""


def parse_image(image_str: str) -> Dict[str, str]:
    """Parse a Docker image string into components.

    Args:
        image_str: Docker image string (e.g., "nginx:latest", "ghcr.io/user/image@sha256:...")

    Returns:
        Dictionary with repo, user, image, tag, and sha keys
    """
    result = {
        "repo": "docker.io",
        "user": "library",
        "image": "",
        "tag": "",
        "sha": "",
    }

    # Extract sha256 digest if present
    if "@" in image_str:
        image_str, result["sha"] = image_str.rsplit("@", 1)

    # Extract tag if present
    if ":" in image_str:
        image_str, result["tag"] = image_str.rsplit(":", 1)

    # Parse the remaining image name
    parts = image_str.split("/")

    if len(parts) == 3:
        result["repo"] = parts[0]
        result["user"] = parts[1]
        result["image"] = parts[2]
    elif len(parts) == 2:
        result["user"] = parts[0]
        result["image"] = parts[1]
    else:
        result["image"] = parts[0]

    return result


def extract_images_from_compose(yaml_content: str, project: str, verbose: bool = False) -> Dict[str, str]:
    """Extract Docker images from a compose file YAML content.

    Args:
        yaml_content: YAML content as string
        project: Project name (used for default container names)
        verbose: If True, print verbose output

    Returns:
        Dictionary mapping container names to image strings
    """
    import yaml

    images: Dict[str, str] = {}

    try:
        compose = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        verbose_print(f"YAML parsing error: {e}", verbose)
        return images

    if not compose or "services" not in compose:
        verbose_print(f"No services found in compose file for project {project}", verbose)
        return images

    services = compose.get("services", {})
    if not isinstance(services, dict):
        verbose_print(f"Services is not a dict for project {project}", verbose)
        return images

    for service_name, service_config in services.items():
        if not isinstance(service_config, dict):
            continue

        image = service_config.get("image")
        if not image:
            verbose_print(f"Service {service_name} has no image", verbose)
            continue

        container_name = service_config.get("container_name")
        if not container_name:
            container_name = f"{project}-{service_name}-1"

        images[container_name] = image
        verbose_print(f"Extracted container {container_name}: {image}", verbose)

    verbose_print(f"Total images extracted for project {project}: {len(images)}", verbose)
    return images


def compare_images(
        section: str,
        project: str,
        change_type: str,
        old_images: Dict[str, str],
        new_images: Dict[str, str],
        verbose: bool = False,
) -> Optional[Dict[str, Any]]:
    """Compare old and new images and return changes.

    Args:
        section: Category (content, infrastructure, etc.)
        project: Project name
        change_type: Type of change (created, deleted, updated)
        old_images: Dictionary of old images
        new_images: Dictionary of new images
        verbose: If True, print verbose output

    Returns:
        Dictionary with change information or None if no changes
    """
    containers: List[Dict[str, Any]] = []

    # Get all unique container names
    all_containers = set(old_images.keys()) | set(new_images.keys())
    verbose_print(f"Comparing {len(all_containers)} container(s) for {section}/{project}", verbose)

    for container in sorted(all_containers):
        old_image = old_images.get(container, "")
        new_image = new_images.get(container, "")

        # Skip if images are identical
        if old_image == new_image:
            verbose_print(f"Container {container}: no change", verbose)
            continue

        verbose_print(f"Container {container}: {old_image or '(none)'} -> {new_image or '(none)'}", verbose)

        old_parsed = parse_image(old_image) if old_image else {
            "repo": "docker.io",
            "user": "library",
            "image": "",
            "tag": "",
            "sha": "",
        }
        new_parsed = parse_image(new_image) if new_image else {
            "repo": "docker.io",
            "user": "library",
            "image": "",
            "tag": "",
            "sha": "",
        }

        # Determine what changed
        updates = []
        if old_parsed["repo"] != new_parsed["repo"]:
            updates.append("repo")
        if old_parsed["user"] != new_parsed["user"]:
            updates.append("user")
        if old_parsed["image"] != new_parsed["image"]:
            updates.append("image")
        if old_parsed["tag"] != new_parsed["tag"]:
            updates.append("tag")
        if old_parsed["sha"] != new_parsed["sha"]:
            updates.append("sha")

        verbose_print(f"  Changes: {', '.join(updates)}", verbose)

        containers.append({
            "container_name": container,
            "old_image": old_image,
            "new_image": new_image,
            "update_types": updates,
            "image": {
                "old": old_parsed,
                "new": new_parsed,
            },
        })

    if not containers:
        verbose_print(f"No image changes found for {section}/{project}", verbose)
        return None

    verbose_print(f"Found {len(containers)} image change(s) for {section}/{project}", verbose)
    return {
        "section": section,
        "project": project,
        "change_type": change_type,
        "changed_images": len(containers),
        "containers": containers,
    }


def process_project_file_change(
        commit: str, status: str, filepath: str, repo_dir: str, verbose: bool = False
) -> Optional[Dict[str, Any]]:
    """Process a single file change in a commit.

    Args:
        commit: Commit SHA
        status: Git status code (A, D, M, etc.)
        filepath: Path to the file
        repo_dir: Repository directory
        verbose: If True, print verbose output

    Returns:
        Dictionary with change information or None
    """
    # Extract section and project from path
    # Path format: compose/{section}/{project}/docker-compose.yml
    path_parts = filepath.split("/")
    if len(path_parts) < 3:
        verbose_print(f"Invalid path format (expected 3+ parts): {filepath}", verbose)
        return None

    section = path_parts[1]
    project = path_parts[2]

    verbose_print(f"Processing {status} change in {section}/{project}: {filepath}", verbose)

    old_content = ""
    new_content = ""
    change_type = ""

    if status == "A":
        change_type = "created"
        new_content = run_git_command(["git", "show", f"{commit}:{filepath}"], check=False, repo_dir=repo_dir,
                                      verbose=verbose)
    elif status == "D":
        change_type = "deleted"
        old_content = run_git_command(["git", "show", f"{commit}^:{filepath}"], check=False, repo_dir=repo_dir,
                                      verbose=verbose)
    else:  # M or any other status
        change_type = "updated"
        old_content = run_git_command(["git", "show", f"{commit}^:{filepath}"], check=False, repo_dir=repo_dir,
                                      verbose=verbose)
        new_content = run_git_command(["git", "show", f"{commit}:{filepath}"], check=False, repo_dir=repo_dir,
                                      verbose=verbose)

    old_images = extract_images_from_compose(old_content, project, verbose) if old_content else {}
    new_images = extract_images_from_compose(new_content, project, verbose) if new_content else {}

    return compare_images(section, project, change_type, old_images, new_images, verbose)


def process_commit(commit: str, repo_dir: str, verbose: bool = False) -> Optional[Dict[str, Any]]:
    """Process a single commit.

    Args:
        commit: Commit SHA
        repo_dir: Repository directory
        verbose: If True, print verbose output

    Returns:
        Dictionary with commit and projects information or None
    """
    print(f"Processing commit {commit}", file=sys.stderr)

    # Get changed files matching docker-compose pattern
    diff_output = run_git_command(
        ["git", "diff", "--name-status", f"{commit}^", commit, "--", "."],
        check=False,
        repo_dir=repo_dir,
        verbose=verbose,
    )

    if not diff_output:
        verbose_print(f"No file changes found in commit {commit}", verbose)
        return None

    # Filter for docker-compose files in compose directory
    pattern = re.compile(r"compose/.*/.*/docker-compose(\.override)?\.ya?ml")
    files = []

    for line in diff_output.split("\n"):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        status, filepath = parts
        if pattern.search(filepath):
            files.append((status, filepath))

    if not files:
        verbose_print(f"No docker-compose files found in commit {commit}", verbose)
        return None

    print(f"Matched {len(files)} docker compose file(s)", file=sys.stderr)

    project_changes = []
    for status, filepath in files:
        result = process_project_file_change(commit, status, filepath, repo_dir, verbose)
        if result:
            project_changes.append(result)
        else:
            print(f"Commit {commit} has no docker image updates", file=sys.stderr)

    if not project_changes:
        verbose_print(f"No project changes extracted from commit {commit}", verbose)
        return None

    verbose_print(f"Extracted {len(project_changes)} project change(s) from commit {commit}", verbose)
    return {
        "commit": commit,
        "projects": project_changes,
    }


def main() -> None:
    """Main entry point."""
    # Check for required dependencies
    try:
        import yaml
    except ImportError:
        print("yaml is required but not installed. Install with: pip install PyYAML", file=sys.stderr)
        sys.exit(1)

    args = parse_arguments()

    verbose_print(
        f"Arguments: repo_dir={args.repo_dir}, remote={args.remote}, branch={args.branch}, tag={args.tag}, skip_fetch={args.skip_fetch}",
        args.verbose)

    # Validate repository exists
    git_dir = Path(args.repo_dir) / ".git"
    if not git_dir.exists():
        print(f"Not a git repository: {args.repo_dir}", file=sys.stderr)
        sys.exit(1)

    verbose_print(f"Repository directory: {args.repo_dir}", args.verbose)

    # Fetch latest changes if not skipped
    if not args.skip_fetch:
        print(
            f"Fetching latest changes from {args.remote}/{args.branch}...",
            file=sys.stderr,
        )
        run_git_command(["git", "fetch", args.remote, args.branch, "--quiet"], repo_dir=args.repo_dir,
                        verbose=args.verbose)
        run_git_command(
            ["git", "fetch", "--force", "--tags", args.remote, args.branch, "--quiet"],
            repo_dir=args.repo_dir,
            verbose=args.verbose,
        )
        verbose_print("Fetch complete", args.verbose)
    else:
        print("Skipping git fetch as --skip-fetch is set", file=sys.stderr)
        verbose_print("Git fetch skipped by user request", args.verbose)

    # Get the last tag/commit and remote HEAD
    verbose_print(f"Resolving tag: {args.tag}", args.verbose)
    last_commit = run_git_command(
        ["git", "rev-parse", args.tag], check=False, repo_dir=args.repo_dir, verbose=args.verbose
    )
    verbose_print(f"Resolved last_commit: {last_commit or '(not found)'}", args.verbose)

    verbose_print(f"Resolving remote HEAD: {args.remote}/{args.branch}", args.verbose)
    remote_head = run_git_command(
        ["git", "rev-parse", f"{args.remote}/{args.branch}"], repo_dir=args.repo_dir, verbose=args.verbose
    )
    verbose_print(f"Resolved remote_head: {remote_head}", args.verbose)

    # Get all commits between last_commit and remote_head
    if last_commit:
        verbose_print(f"Getting commits between {last_commit} and {remote_head}", args.verbose)
        commits_output = run_git_command(
            ["git", "rev-list", "--reverse", f"{last_commit}..{remote_head}"],
            check=False,
            repo_dir=args.repo_dir,
            verbose=args.verbose,
        )
    else:
        verbose_print("No last_commit found, will use all commits to remote_head", args.verbose)
        commits_output = ""

    if not commits_output:
        print("No new commits to process.", file=sys.stderr)
        output_json = "[]"
    else:
        commits = [c for c in commits_output.split("\n") if c.strip()]
        commit_count = len(commits)

        print(f"Processing {commit_count} new commit(s)", file=sys.stderr)
        print(f"Oldest: {last_commit or 'N/A'}", file=sys.stderr)
        print(f"Newest: {remote_head}", file=sys.stderr)

        verbose_print(f"Starting to process {commit_count} commit(s)", args.verbose)

        full_output: List[Dict[str, Any]] = []
        for idx, commit in enumerate(commits, 1):
            verbose_print(f"[{idx}/{commit_count}] Processing {commit}", args.verbose)
            commit_result = process_commit(commit, args.repo_dir, args.verbose)
            if commit_result:
                full_output.append(commit_result)
                verbose_print(f"[{idx}/{commit_count}] Extracted {len(commit_result['projects'])} project change(s)",
                              args.verbose)
            else:
                verbose_print(f"[{idx}/{commit_count}] No changes in this commit", args.verbose)

        verbose_print(f"Processing complete. Total commits with changes: {len(full_output)}", args.verbose)

        # Output results
        output_json = json.dumps(full_output, indent=2)

    if args.output:
        verbose_print(f"Writing results to file: {args.output}", args.verbose)
        try:
            with open(args.output, "w") as f:
                f.write(output_json)
            print(f"Results written to: {args.output}", file=sys.stderr)
            verbose_print(f"Successfully wrote {len(output_json)} bytes to {args.output}", args.verbose)
        except IOError as e:
            print(f"Error writing to output file {args.output}: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        verbose_print("Outputting results to stdout", args.verbose)
        print(output_json)


if __name__ == "__main__":
    main()
