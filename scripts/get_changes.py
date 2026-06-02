#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import sys
import logging
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

    logging.getLogger(__name__).debug("Running git command: %s", ' '.join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )
        if verbose and result.stdout:
            logging.getLogger(__name__).debug("Git output (%d chars): %s", len(result.stdout), result.stdout[:200])
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if check:
            logger = logging.getLogger(__name__)
            logger.error("Git command failed: %s", ' '.join(cmd))
            logger.error("Error: %s", e.stderr)
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
        logging.getLogger(__name__).debug(f"YAML parsing error: {e}")
        return images

    if not compose or "services" not in compose:
        logging.getLogger(__name__).debug(f"No services found in compose file for project {project}")
        return images

    services = compose.get("services", {})
    if not isinstance(services, dict):
        logging.getLogger(__name__).debug(f"Services is not a dict for project {project}")
        return images

    for service_name, service_config in services.items():
        if not isinstance(service_config, dict):
            continue

        image = service_config.get("image")
        if not image:
            logging.getLogger(__name__).debug(f"Service {service_name} has no image")
            continue

        container_name = service_config.get("container_name")
        if not container_name:
            container_name = f"{project}-{service_name}-1"

        images[container_name] = image
        logging.getLogger(__name__).debug(f"Extracted container {container_name}: {image}")

    logging.getLogger(__name__).debug(f"Total images extracted for project {project}: {len(images)}")
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
    logging.getLogger(__name__).debug(f"Comparing {len(all_containers)} container(s) for {section}/{project}")

    for container in sorted(all_containers):
        old_image = old_images.get(container, "")
        new_image = new_images.get(container, "")

        # Skip if images are identical
        if old_image == new_image:
            logging.getLogger(__name__).debug(f"Container {container}: no change")
            continue

        logging.getLogger(__name__).debug(f"Container {container}: {old_image or '(none)'} -> {new_image or '(none)'}")

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

        logging.getLogger(__name__).debug(f"  Changes: {', '.join(updates)}")

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
        logging.getLogger(__name__).debug(f"No image changes found for {section}/{project}")
        return None

    logging.getLogger(__name__).debug(f"Found {len(containers)} image change(s) for {section}/{project}")
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
        logging.getLogger(__name__).debug(f"Invalid path format (expected 3+ parts): {filepath}")
        return None

    section = path_parts[1]
    project = path_parts[2]

    logging.getLogger(__name__).debug(f"Processing {status} change in {section}/{project}: {filepath}")

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
    logger = logging.getLogger(__name__)
    logger.info(f"Processing commit {commit}")

    # Get changed files matching docker-compose pattern
    diff_output = run_git_command(
        ["git", "diff", "--name-status", f"{commit}^", commit, "--", "."],
        check=False,
        repo_dir=repo_dir,
        verbose=verbose,
    )

    if not diff_output:
        logging.getLogger(__name__).debug(f"No file changes found in commit {commit}")
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
        logging.getLogger(__name__).debug(f"No docker-compose files found in commit {commit}")
        return None

    logger.info(f"Matched {len(files)} docker compose file(s)")

    project_changes = []
    for status, filepath in files:
        result = process_project_file_change(commit, status, filepath, repo_dir, verbose)
        if result:
            project_changes.append(result)
        else:
            logger.info(f"Commit {commit} has no docker image updates")

    if not project_changes:
        logging.getLogger(__name__).debug(f"No project changes extracted from commit {commit}")
        return None

    logging.getLogger(__name__).debug(f"Extracted {len(project_changes)} project change(s) from commit {commit}")
    return {
        "commit": commit,
        "projects": project_changes,
    }


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Configure logging early so we can use proper logging calls everywhere.
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    logger = logging.getLogger(__name__)

    # Check for required dependencies
    try:
        import yaml
    except ImportError:
        logger.error("yaml is required but not installed. Install with: pip install PyYAML")
        sys.exit(1)

    logger.debug(
        "Arguments: repo_dir=%s, remote=%s, branch=%s, tag=%s, skip_fetch=%s",
        args.repo_dir,
        args.remote,
        args.branch,
        args.tag,
        args.skip_fetch,
    )

    # Validate repository exists
    git_dir = Path(args.repo_dir) / ".git"
    if not git_dir.exists():
        logger.error("Not a git repository: %s", args.repo_dir)
        sys.exit(1)

    logger.debug(f"Repository directory: {args.repo_dir}")

    # Fetch latest changes if not skipped
    if not args.skip_fetch:
        logger.info("Fetching latest changes from %s/%s...", args.remote, args.branch)
        run_git_command(["git", "fetch", args.remote, args.branch, "--quiet"], repo_dir=args.repo_dir,
                        verbose=args.verbose)
        run_git_command(
            ["git", "fetch", "--force", "--tags", args.remote, args.branch, "--quiet"],
            repo_dir=args.repo_dir,
            verbose=args.verbose,
        )
        logger.debug("Fetch complete")
    else:
        logger.info("Skipping git fetch as --skip-fetch is set")
        logger.debug("Git fetch skipped by user request")

    # Get the last tag/commit and remote HEAD
    logger.debug(f"Resolving tag: {args.tag}")
    last_commit = run_git_command(
        ["git", "rev-parse", args.tag], check=False, repo_dir=args.repo_dir, verbose=args.verbose
    )
    logger.debug(f"Resolved last_commit: {last_commit or '(not found)'}")

    logger.debug(f"Resolving remote HEAD: {args.remote}/{args.branch}")
    remote_head = run_git_command(
        ["git", "rev-parse", f"{args.remote}/{args.branch}"], repo_dir=args.repo_dir, verbose=args.verbose
    )
    logger.debug(f"Resolved remote_head: {remote_head}")

    # Get all commits between last_commit and remote_head
    if last_commit:
        logger.debug(f"Getting commits between {last_commit} and {remote_head}")
        commits_output = run_git_command(
            ["git", "rev-list", "--reverse", f"{last_commit}..{remote_head}"],
            check=False,
            repo_dir=args.repo_dir,
            verbose=args.verbose,
        )
    else:
        logger.debug("No last_commit found, will use all commits to remote_head")
        commits_output = ""

    if not commits_output:
        logger.info("No new commits to process.")
        output_json = "[]"
    else:
        commits = [c for c in commits_output.split("\n") if c.strip()]
        commit_count = len(commits)

        logger.info("Processing %d new commit(s)", commit_count)
        logger.info("Oldest: %s", last_commit or 'N/A')
        logger.info("Newest: %s", remote_head)

        logger.debug(f"Starting to process {commit_count} commit(s)")

        full_output: List[Dict[str, Any]] = []
        for idx, commit in enumerate(commits, 1):
            logger.debug(f"[{idx}/{commit_count}] Processing {commit}")
            commit_result = process_commit(commit, args.repo_dir, args.verbose)
            if commit_result:
                full_output.append(commit_result)
                logger.debug(f"[{idx}/{commit_count}] Extracted {len(commit_result['projects'])} project change(s)")
            else:
                logger.debug(f"[{idx}/{commit_count}] No changes in this commit")

        logger.debug(f"Processing complete. Total commits with changes: {len(full_output)}")

        # Output results
        output_json = json.dumps(full_output, indent=2)

    if args.output:
        logger.debug(f"Writing results to file: {args.output}")
        try:
            with open(args.output, "w") as f:
                f.write(output_json)
            logger.info("Results written to: %s", args.output)
            logger.debug(f"Successfully wrote {len(output_json)} bytes to {args.output}")
        except IOError as e:
            logger.error("Error writing to output file %s: %s", args.output, e)
            sys.exit(1)
    else:
        logger.debug("Outputting results to stdout")
        # JSON must go to stdout unchanged
        print(output_json)


if __name__ == "__main__":
    main()
