"""Incremental file indexer implementation."""

import os
import tempfile
from pathlib import Path
from typing import Dict

from src.exceptions import IndexerError, ManifestWriteError
from src.hasher import compute_sha256
from src.manifest import format_manifest, load_manifest


def sync_index(root: Path, manifest_path: Path) -> dict:
    """Synchronize the manifest with current regular .txt files in root."""
    root = Path(root)
    if root.is_symlink():
        raise IndexerError(f"Root path is not a directory: {root}")
    root = root.resolve()
    manifest_path = Path(manifest_path).resolve()

    if not root.is_dir():
        raise IndexerError(f"Root path is not a directory: {root}")

    # Do not write anything until the existing manifest has been validated.
    old_manifest = load_manifest(manifest_path)
    current_files: Dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        # Explicitly prune directory links, including links to locations inside root.
        dirnames[:] = [
            dirname for dirname in dirnames
            if not (Path(dirpath) / dirname).is_symlink()
        ]
        for fname in filenames:
            if not fname.endswith(".txt"):
                continue

            file_path = Path(dirpath) / fname
            try:
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                rel_path = file_path.relative_to(root).as_posix()
                current_files[rel_path] = compute_sha256(file_path)
            except OSError:
                # A file may disappear or become unreadable after it is listed.
                continue

    added = []
    modified = []
    unchanged = []
    deleted = []

    for path, new_hash in current_files.items():
        if path not in old_manifest:
            added.append(path)
        elif old_manifest[path] != new_hash:
            modified.append(path)
        else:
            unchanged.append(path)

    for path in old_manifest:
        if path not in current_files:
            deleted.append(path)

    temp_path = None
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_path.parent,
            prefix=f".{manifest_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(format_manifest(current_files))
            temp_file.flush()
            os.fsync(temp_file.fileno())

        os.replace(temp_path, manifest_path)
        temp_path = None
    except OSError as exc:
        raise ManifestWriteError(f"Failed to write manifest: {manifest_path}") from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": sorted(deleted),
        "unchanged": sorted(unchanged),
    }
