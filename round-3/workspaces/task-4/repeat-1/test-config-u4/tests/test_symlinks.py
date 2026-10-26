"""Public tests for symlink handling and loop prevention."""

import os
import tempfile
import unittest
from pathlib import Path

from src.indexer import sync_index


class TestSymlinks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.manifest_path = Path(self.temp_dir.name) / "manifest.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_symlinks_are_ignored(self):
        """Symlinks pointing to files or directories must be ignored."""
        real_file = self.root / "real.txt"
        real_file.write_text("real content", encoding="utf-8")

        outside_file = Path(self.temp_dir.name) / "outside.txt"
        outside_file.write_text("outside", encoding="utf-8")

        # Symlink to outside file
        link_outside = self.root / "link_outside.txt"
        link_outside.symlink_to(outside_file)

        # Symlink to real file
        link_inside = self.root / "link_inside.txt"
        link_inside.symlink_to(real_file)

        res = sync_index(self.root, self.manifest_path)
        # Only the real file must be indexed
        self.assertEqual(res["added"], ["real.txt"])

    def test_symlink_loop_avoidance(self):
        """Recursive directory symlinks must never cause an infinite loop."""
        (self.root / "data.txt").write_text("data", encoding="utf-8")
        sub = self.root / "subdir"
        sub.mkdir()
        (sub / "subdata.txt").write_text("subdata", encoding="utf-8")

        # Recursive symlink pointing to parent or self
        loop_link = sub / "loop"
        loop_link.symlink_to(self.root, target_is_directory=True)

        res = sync_index(self.root, self.manifest_path)
        self.assertEqual(res["added"], ["data.txt", "subdir/subdata.txt"])


if __name__ == "__main__":
    unittest.main()
