"""Public unit tests for incremental file indexer."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from src.exceptions import CorruptManifestError
from src.indexer import sync_index


class TestIndexer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.manifest_path = Path(self.temp_dir.name) / "manifest.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_first_indexing_run(self):
        (self.root / "file1.txt").write_text("hello", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "file2.txt").write_text("world", encoding="utf-8")

        res = sync_index(self.root, self.manifest_path)
        self.assertEqual(sorted(res["added"]), ["file1.txt", "sub/file2.txt"])
        self.assertEqual(res["modified"], [])
        self.assertEqual(res["deleted"], [])
        self.assertEqual(res["unchanged"], [])
        self.assertTrue(self.manifest_path.is_file())

    def test_second_run_no_changes(self):
        (self.root / "file1.txt").write_text("hello", encoding="utf-8")
        sync_index(self.root, self.manifest_path)

        res2 = sync_index(self.root, self.manifest_path)
        self.assertEqual(res2["added"], [])
        self.assertEqual(res2["modified"], [])
        self.assertEqual(res2["deleted"], [])
        self.assertEqual(res2["unchanged"], ["file1.txt"])

    def test_modification_detected(self):
        f = self.root / "file1.txt"
        f.write_text("initial", encoding="utf-8")
        sync_index(self.root, self.manifest_path)

        f.write_text("updated", encoding="utf-8")
        res = sync_index(self.root, self.manifest_path)
        self.assertEqual(res["modified"], ["file1.txt"])
        self.assertEqual(res["added"], [])
        self.assertEqual(res["deleted"], [])

    def test_deletion_detected(self):
        f = self.root / "file1.txt"
        f.write_text("content", encoding="utf-8")
        sync_index(self.root, self.manifest_path)

        f.unlink()
        res = sync_index(self.root, self.manifest_path)
        self.assertEqual(res["deleted"], ["file1.txt"])

    def test_corrupt_manifest_must_raise_error(self):
        """Corrupt manifest must raise CorruptManifestError and not overwrite existing file."""
        self.manifest_path.write_text("INVALID JSON NOT A DICT {", encoding="utf-8")
        (self.root / "file1.txt").write_text("content", encoding="utf-8")

        with self.assertRaises(CorruptManifestError):
            sync_index(self.root, self.manifest_path)

        # File must remain untouched
        self.assertEqual(self.manifest_path.read_text(encoding="utf-8"), "INVALID JSON NOT A DICT {")


if __name__ == "__main__":
    unittest.main()
