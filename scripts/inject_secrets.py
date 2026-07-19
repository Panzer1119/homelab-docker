#!/usr/bin/env python3
"""
Enhanced secrets injection script with multiple resolution modes.

Processes ref.* files and replaces 1Password secret references (op://...)
with actual values from various sources: cache, interactive input, or the
1Password Python SDK using a service account token.
"""

import argparse
import asyncio
import getpass
import json
import logging
import os
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Set, Tuple


class ResolutionMode(Enum):
    """Mode for resolving secret references."""
    CACHE = "cache"
    INTERACTIVE = "interactive"
    OP = "op"
    HYBRID = "hybrid"  # Use cache first, then ask interactively if empty


INTEGRATION_NAME = "homelab-docker secrets injector"
INTEGRATION_VERSION = "1.0.0"
SECRET_REFERENCE_PATTERN = re.compile(
    r"\{\{\s*(?P<enclosed_ref>op://\S.+?)\s*\}\}|(?P<plain_ref>op://\S+?)(?=\s|[\]}>)'\"`,;:]|$)",
)


# Configure logging
def setup_logging(debug: bool = False, verbose: bool = False) -> None:
    """Configure logging with appropriate level."""
    if debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        format='%(asctime)s - %(name)-10s - %(levelname)-7s - %(message)s',
        level=level
    )


logger = logging.getLogger(__name__)


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Inject secrets into ref.* files by replacing op:// references',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --mode interactive compose/content/immich/
  %(prog)s --mode cache --cache-file ./secrets.json compose/
  %(prog)s --mode op --dry-run compose/
  %(prog)s -v --mode hybrid compose/content/ compose/media/
        """
    )

    # Positional arguments
    parser.add_argument(
        'paths',
        nargs='+',
        help='One or more paths (files or directories) to process'
    )

    # Mode selection
    parser.add_argument(
        '--mode',
        choices=['cache', 'interactive', 'op', 'hybrid'],
        default='hybrid',
        help='Resolution mode (default: hybrid; op uses the 1Password SDK)'
    )

    # 1Password service account token
    parser.add_argument(
        '--service-account-token',
        help='1Password service account token (overrides OP_SERVICE_ACCOUNT_TOKEN)'
    )

    parser.add_argument(
        '-t', '--token',
        dest='service_account_token',
        help='Alias for --service-account-token'
    )

    # Cache file
    parser.add_argument(
        '--cache-file',
        default='~/.homelab_secrets_cache.json',
        help='Path to cache file (default: ~/.homelab_secrets_cache.json)'
    )

    # Dry-run mode
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )

    # Logging options
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='Enable debug logging'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    # Skip confirmation
    parser.add_argument(
        '--no-confirm',
        action='store_true',
        help='Skip confirmation prompts'
    )

    return parser.parse_args()


def find_ref_files(paths: list) -> Tuple[Set[Path], Dict[Path, Path]]:
    """
    Find all ref.* files in the given paths.

    Returns:
        Tuple of (ref_files set, mapping from ref_file to output_file)
    """
    ref_files = set()
    output_mapping = {}

    for path_str in paths:
        path = Path(path_str).expanduser()

        if not path.exists():
            logger.warning(f"Path does not exist: {path}")
            continue

        if path.is_file():
            if path.name.startswith('ref.'):
                ref_files.add(path)
                output_mapping[path] = _get_output_path(path)
            else:
                logger.debug(f"Skipping non-ref file: {path}")
        else:
            # Recursively find ref.* files
            for file_path in path.rglob('ref.*'):
                if file_path.is_file():
                    ref_files.add(file_path)
                    output_mapping[file_path] = _get_output_path(file_path)

    logger.info(f"Found {len(ref_files)} ref.* files")
    return ref_files, output_mapping


def _get_output_path(ref_path: Path) -> Path:
    """
    Convert ref.FILENAME to FILENAME (or ref.env to .env).

    Args:
        ref_path: Path to ref.* file

    Returns:
        Output path with appropriate name
    """
    filename = ref_path.name
    assert filename.startswith('ref.'), f"Expected ref.* file: {filename}"

    # Remove 'ref.' prefix
    output_name = filename[4:]

    # Special case: ref.env -> .env
    if filename == 'ref.env':
        output_name = '.env'

    return ref_path.parent / output_name


def collect_secret_references(files: Set[Path]) -> Dict[str, Set[str]]:
    """
    Collect all 1Password secret references from files.

    Returns a dict mapping canonical secret references to the exact tokens that
    should be replaced in files (for example, enclosed refs keep their braces).
    """
    secrets: Dict[str, Set[str]] = {}

    logger.info("Collecting secret references from files...")
    for file_path in files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                matches = list(SECRET_REFERENCE_PATTERN.finditer(content))
                logger.debug(f"Processing {file_path}: found {len(matches)} references")
                for match in matches:
                    canonical_ref = (match.group('enclosed_ref') or match.group('plain_ref') or '').strip()
                    exact_token = match.group(0)
                    if canonical_ref:
                        secrets.setdefault(canonical_ref, set()).add(exact_token)
                        logger.debug(
                            f"  Found reference: {canonical_ref!r} (token: {exact_token!r}, repr: {repr(exact_token)})")
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")

    logger.info(f"Collected {len(secrets)} unique secret references")
    return secrets


def load_cache(cache_file: str) -> Dict[str, str]:
    """Load cached secrets from JSON file."""
    cache_path = Path(cache_file).expanduser()

    if not cache_path.exists():
        logger.debug(f"Cache file not found: {cache_path}")
        return {}

    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache = json.load(f)
            logger.info(f"Loaded {len(cache)} secrets from cache: {cache_path}")
            return cache
    except Exception as e:
        logger.error(f"Error loading cache file {cache_path}: {e}")
        return {}


def save_cache(cache_file: str, secrets: Dict[str, str]) -> None:
    """Save secrets to cache file."""
    cache_path = Path(cache_file).expanduser()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(secrets, f, indent=2)
        logger.info(f"Saved {len(secrets)} secrets to cache: {cache_path}")
    except Exception as e:
        logger.error(f"Error saving cache file {cache_path}: {e}")


def get_service_account_token(cli_token: Optional[str] = None) -> Optional[str]:
    """Return a service-account token from CLI, env, or interactive prompt."""
    token = (cli_token or os.getenv('OP_SERVICE_ACCOUNT_TOKEN') or '').strip()
    if token:
        return token

    try:
        token = getpass.getpass('Enter OP_SERVICE_ACCOUNT_TOKEN: ').strip()
    except (EOFError, KeyboardInterrupt):
        logger.warning('User cancelled token input')
        return None

    return token or None


async def authenticate_onepassword_client(token: str):
    """Authenticate and return a 1Password SDK client."""
    try:
        from onepassword.client import Client
    except ImportError as exc:
        raise RuntimeError(
            'The onepassword-sdk package is required for --mode op_cli. '
            'Install it with: pip install onepassword-sdk'
        ) from exc

    return await Client.authenticate(
        auth=token,
        integration_name=INTEGRATION_NAME,
        integration_version=INTEGRATION_VERSION,
    )


async def resolve_with_op(secret_refs: Set[str], op_client) -> Dict[str, str]:
    """Resolve secret references using the 1Password Python SDK in one batch."""
    resolved: Dict[str, str] = {}

    try:
        response = await op_client.secrets.resolve_all(sorted(secret_refs))
    except Exception as e:
        logger.error(f"Failed to resolve secrets with 1Password SDK: {e}")
        return resolved

    individual_responses = getattr(response, 'individual_responses', {})
    for secret_ref in sorted(secret_refs):
        result = individual_responses.get(secret_ref)
        if result is None:
            logger.warning(f"No SDK response returned for: {secret_ref}")
            continue

        error = getattr(result, 'error', None)
        if error:
            logger.error(f"Failed to resolve {secret_ref} with 1Password SDK: {error}")
            continue

        content = getattr(result, 'content', None)
        secret = getattr(content, 'secret', None) if content is not None else None
        if secret:
            resolved[secret_ref] = secret.strip()
            logger.debug(f"Resolved: {secret_ref} -> ****")
        else:
            logger.warning(f"Empty secret returned for: {secret_ref}")

    return resolved


def prompt_for_secret(secret_ref: str) -> Optional[str]:
    """Interactively ask user for a secret value."""
    try:
        value = input(f"Enter value for '{secret_ref}': ").strip()
        return value if value else None
    except (EOFError, KeyboardInterrupt):
        logger.warning("User cancelled input")
        return None


async def resolve_secrets(
        secrets: Dict[str, Set[str]],
        cache: Dict[str, str],
        mode: ResolutionMode,
        dry_run: bool = False,
        op_client=None,
) -> Dict[str, str]:
    """
    Resolve all secret references based on mode.

    Returns a dict of reference -> resolved_value
    """
    resolved = {}

    if mode == ResolutionMode.OP:
        if op_client is None:
            logger.error('1Password client is not available for SDK resolution')
            return resolved

        return await resolve_with_op(set(secrets.keys()), op_client)

    # Process secrets alphabetically
    for secret_ref in sorted(secrets.keys()):
        resolved_value = None

        if mode == ResolutionMode.CACHE:
            resolved_value = cache.get(secret_ref)
            if not resolved_value:
                logger.warning(f"Secret not in cache: {secret_ref}")
        elif mode == ResolutionMode.INTERACTIVE:
            resolved_value = prompt_for_secret(secret_ref)
        elif mode == ResolutionMode.HYBRID:
            # Try cache first
            resolved_value = cache.get(secret_ref)
            if not resolved_value:
                # Fall back to interactive
                resolved_value = prompt_for_secret(secret_ref)

        if resolved_value:
            resolved[secret_ref] = resolved_value
            logger.debug(f"Resolved: {secret_ref} -> ****")
        else:
            logger.warning(f"Could not resolve: {secret_ref}")

    return resolved


def process_file(
        ref_path: Path,
        output_path: Path,
        resolved_secrets: Dict[str, str],
        reference_tokens: Dict[str, Set[str]],
        dry_run: bool = False
) -> bool:
    """
    Process a single ref.* file and create output with resolved secrets.

    Returns True if successful, False otherwise.
    """
    try:
        with open(ref_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Replace the exact token that appeared in the file (enclosed or plain).
        processed_content = content
        for secret_ref, value in resolved_secrets.items():
            for token in sorted(reference_tokens.get(secret_ref, set()), key=len, reverse=True):
                processed_content = processed_content.replace(token, value)

        if content == processed_content:
            logger.warning(f"[process_file] Content UNCHANGED after all replacements for: {ref_path}")
        else:
            logger.debug(f"[process_file] Content was modified successfully for: {ref_path}")

        if dry_run:
            logger.info(f"[DRY-RUN] Would create: {output_path}")
            if processed_content != content:
                logger.debug(f"[DRY-RUN] Content would be modified")
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            exists: bool = output_path.exists()
            is_file: bool = output_path.is_file() if exists else False
            if exists and not is_file:
                logger.error(f"Output path exists and is not a file: {output_path}")
                return False

            if exists:
                with open(output_path, 'r', encoding='utf-8') as f:
                    existing_content = f.read()
                if existing_content == processed_content:
                    logger.info(f"Skipped (unchanged): {output_path}")
                    return True

            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(processed_content)
            if not exists:
                logger.info(f"Created: {output_path}")
            else:
                logger.info(f"Updated: {output_path}")

        return True
    except Exception as e:
        logger.error(f"Error processing file {ref_path}: {e}")
        return False


def main() -> int:
    """Main entry point."""
    args = parse_arguments()
    setup_logging(debug=args.debug, verbose=args.verbose)

    logger.debug(f"Arguments: {args}")

    # Convert mode string to enum
    mode = ResolutionMode(args.mode)

    # Find ref.* files
    ref_files, output_mapping = find_ref_files(args.paths)
    if not ref_files:
        logger.error("No ref.* files found in the provided paths")
        return 1

    # Collect secret references
    secrets = collect_secret_references(ref_files)
    if not secrets:
        logger.warning("No secret references found")

    # Load cache
    cache = load_cache(args.cache_file)

    op_client = None
    if mode == ResolutionMode.OP:
        token = get_service_account_token(args.service_account_token)
        if not token:
            logger.error('A service account token is required for op mode')
            return 1

        try:
            op_client = asyncio.run(authenticate_onepassword_client(token))
        except Exception as e:
            logger.error(f'Failed to authenticate with 1Password SDK: {e}')
            return 1

    # Resolve secrets
    resolved_secrets = asyncio.run(resolve_secrets(secrets, cache, mode, args.dry_run, op_client))

    # Check if all secrets were resolved
    unresolved = set(secrets.keys()) - set(resolved_secrets.keys())
    if unresolved:
        logger.warning(f"Could not resolve {len(unresolved)} secrets:")
        for ref in sorted(unresolved):
            logger.warning(f"  - {ref}")

    # Show summary
    logger.info(f"Resolved {len(resolved_secrets)}/{len(secrets)} secrets")

    if args.dry_run:
        logger.info("[DRY-RUN] Would process the following files:")
        for ref_path, output_path in sorted(output_mapping.items()):
            logger.info(f"  {ref_path} -> {output_path}")
        return 0

    # Confirmation
    if not args.no_confirm and ref_files:
        print(f"\nAbout to process {len(ref_files)} files.")
        response = input("Continue? (y/N): ").strip().lower()
        if response != 'y':
            logger.info("Cancelled by user")
            return 0

    # Process files
    success_count = 0
    for ref_path, output_path in output_mapping.items():
        if process_file(ref_path, output_path, resolved_secrets, secrets, args.dry_run):
            success_count += 1

    # Update cache with resolved secrets
    updated_cache = cache.copy()
    updated_cache.update(resolved_secrets)
    save_cache(args.cache_file, updated_cache)

    logger.info(f"Successfully processed {success_count}/{len(ref_files)} files")
    return 0 if success_count == len(ref_files) else 1


if __name__ == '__main__':
    sys.exit(main())
