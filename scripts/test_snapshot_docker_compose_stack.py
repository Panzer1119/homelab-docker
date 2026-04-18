import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_docker_compose_stack import ensure_requirements, parse_args, parse_image_reference, should_skip_up_in_worktree


class SnapshotCliTests(unittest.TestCase):
    def test_override_files_are_repeatable(self) -> None:
        args = parse_args(["-f", "docker-compose.a.yml", "-f", "docker-compose.b.yml"])
        self.assertEqual(args.override_files, ["docker-compose.a.yml", "docker-compose.b.yml"])

    def test_hold_defaults_enabled(self) -> None:
        args = parse_args([])
        self.assertTrue(args.hold_snapshots)
        self.assertEqual(args.hold_name, "stack-checkpoint")

    def test_hold_can_be_disabled(self) -> None:
        args = parse_args(["--no-hold-snapshots"])
        self.assertFalse(args.hold_snapshots)

    def test_parse_image_reference_with_digest(self) -> None:
        image, tag, digest = parse_image_reference("ghcr.io/linuxserver/jellyfin:10.10.7@sha256:deadbeef")
        self.assertEqual(image, "ghcr.io/linuxserver/jellyfin")
        self.assertEqual(tag, "10.10.7")
        self.assertEqual(digest, "deadbeef")

    def test_parse_image_reference_defaults(self) -> None:
        image, tag, digest = parse_image_reference("redis")
        self.assertEqual(image, "docker.io/_/redis")
        self.assertEqual(tag, "latest")
        self.assertEqual(digest, "")

    def test_pwd_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            compose = Path(tmp) / "docker-compose.yml"
            compose.write_text(
                "services:\n"
                "  app:\n"
                "    volumes:\n"
                "      - ${PWD}/data:/data\n",
                encoding="utf-8",
            )
            self.assertTrue(should_skip_up_in_worktree(compose, None))

    @patch("snapshot_docker_compose_stack.run_command")
    @patch("snapshot_docker_compose_stack.shutil.which", return_value="/usr/bin/mock")
    def test_ensure_requirements_scopes_git_checks_to_repo(self, _which_mock, run_command_mock) -> None:
        repo_root = Path("/tmp/homelab-docker")
        ensure_requirements(repo_root=repo_root, dry_run=True, use_worktree=True)

        calls = [call.args[0] for call in run_command_mock.call_args_list]
        self.assertIn(["docker", "ps"], calls)
        self.assertIn(["git", "-C", str(repo_root), "worktree", "list"], calls)


if __name__ == "__main__":
    unittest.main()

