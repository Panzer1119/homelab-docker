import tempfile
import unittest
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))

from snapshot_docker_compose_stack import parse_image_reference, should_skip_up_in_worktree


class SnapshotCliTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

